"""Maya integration coverage for the report-only MMD control-rig analyzer."""

import os
from pathlib import Path
from unittest import mock

from maya import cmds

from mmd_tools.core import settings
from mmd_tools.core.constants import ATTR_MMD_BONE_INDEX, ATTR_MMD_CONTROL_RIG_JSON
from mmd_tools.core.mmd_control_rig_builder import (
    MmdControlRigBuildError,
    build_mmd_control_rig,
    read_mmd_control_rig_metadata,
    remove_mmd_control_rig,
)
from mmd_tools.core.mmd_control_rig_motion import (
    bake_mmd_control_rig,
    enter_mmd_control_rig_edit,
)
from mmd_tools.core.mmd_control_rig_analyzer import (
    INPUT_IK_CONTROLLER,
    INPUT_SOLVER_OUTPUT,
    STATUS_READY,
    analyze_mmd_control_rig,
)
from mmd_tools.io.mmd_importer import import_mmd_file
from tests.common.maya_test_base import MayaTestBase


_TEST_DATA = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
)
_PMX_PATH = os.path.join(_TEST_DATA, "mmt_test_model.pmx")
_VMD_PATH = os.path.join(_TEST_DATA, "mmt_test_model_test_motion.vmd")


class TestMmdControlRigAnalyzerIntegration(MayaTestBase):
    """Verify the real rig-mode fixture produces a buildable MVP spec."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._previous_skip_shader_override = os.environ.get(
            "MMD_TOOLS_SKIP_SHADER_OVERRIDE"
        )
        os.environ["MMD_TOOLS_SKIP_SHADER_OVERRIDE"] = "1"
        root = Path(__file__).resolve().parents[2]
        maya_version = str(cmds.about(version=True)).split(".", 1)[0]
        cpp_plugin = root / "plug-ins" / maya_version / "Debug" / "mmd_tools_cpp.mll"
        if not cpp_plugin.exists():
            raise RuntimeError(
                f"Maya {maya_version} Debug C++ plugin is required; run "
                f"'uvx nox -s cpp_build -- --maya {maya_version} --config Debug'"
            )
        python_plugin = root / "mmd_tools" / "plugin_main.py"
        owned_plugins = []
        for plugin in (cpp_plugin, python_plugin):
            plugin_path = str(plugin)
            if cmds.pluginInfo(plugin_path, query=True, loaded=True):
                continue
            cmds.loadPlugin(plugin_path, quiet=True)
            owned_plugins.append(plugin_path)
        # The Python plugin detects and depends on the already loaded C++ rig
        # node provider, so unload it first at class teardown.
        cls.plugins_loaded = list(reversed(owned_plugins))

    @classmethod
    def tearDownClass(cls):
        try:
            super().tearDownClass()
        finally:
            if cls._previous_skip_shader_override is None:
                os.environ.pop("MMD_TOOLS_SKIP_SHADER_OVERRIDE", None)
            else:
                os.environ[
                    "MMD_TOOLS_SKIP_SHADER_OVERRIDE"
                ] = cls._previous_skip_shader_override

    def setUp(self):
        super().setUp()
        self._create_shaders = settings.get("import.model.create_mmd_shaders", True)
        self._add_semistandard = settings.get(
            "import.rig.add_semi_standard_bones",
            False,
        )
        settings.set("import.model.create_mmd_shaders", False)
        settings.set("import.rig.add_semi_standard_bones", False)

    def tearDown(self):
        settings.set("import.model.create_mmd_shaders", self._create_shaders)
        settings.set("import.rig.add_semi_standard_bones", self._add_semistandard)
        super().tearDown()

    def _import_fixture(self, **extra_options):
        options = {
            "setup_rig": True,
            "setup_bone_orientation": True,
            "import_physics": False,
        }
        options.update(extra_options)
        root = import_mmd_file(_PMX_PATH, options=options)
        self.assertTrue(root)
        return root

    def test_mmt_rig_fixture_classifies_mvp_without_mutating_scene(self):
        root = self._import_fixture()
        nodes_before = set(cmds.ls(long=True) or [])

        spec = analyze_mmd_control_rig(root)

        self.assertEqual(set(cmds.ls(long=True) or []), nodes_before)
        roles = spec.roles_by_name
        for role in ("master", "center", "groove", "left_foot_ik", "right_foot_ik"):
            self.assertEqual(roles[role].status, STATUS_READY, role)
        self.assertEqual(
            roles["left_foot_ik"].binding.input_kind,
            INPUT_IK_CONTROLLER,
        )
        self.assertEqual(
            roles["right_foot_ik"].binding.input_kind,
            INPUT_IK_CONTROLLER,
        )
        self.assertTrue(roles["left_foot_ik"].binding.ik_solvers)
        self.assertTrue(roles["right_foot_ik"].binding.ik_solvers)
        self.assertTrue(spec.can_build_mvp)
        self.assertTrue(spec.display_frames)

        solver_outputs = [
            binding
            for binding in spec.bones
            if binding.input_kind == INPUT_SOLVER_OUTPUT
        ]
        self.assertTrue(solver_outputs)
        self.assertTrue(all(binding.blocked for binding in solver_outputs))

    def test_builder_is_detached_idempotent_reopenable_and_removable(self):
        root = self._import_fixture()
        root = (cmds.ls(root, long=True) or [root])[0]
        joints = [
            joint
            for joint in cmds.listRelatives(
                root,
                allDescendents=True,
                type="joint",
                fullPath=True,
            )
            or []
            if cmds.attributeQuery(ATTR_MMD_BONE_INDEX, node=joint, exists=True)
        ]
        matrices_before = {
            joint: tuple(cmds.getAttr(f"{joint}.worldMatrix[0]"))
            for joint in joints
        }
        cycles_before = sorted(cmds.cycleCheck(all=True, list=True) or [])

        result = build_mmd_control_rig(root)

        self.assertTrue(result.created)
        self.assertTrue(
            {"master", "center", "groove", "left_foot_ik", "right_foot_ik"}
            .issubset(result.controls)
        )
        self.assertFalse(cmds.listRelatives(result.control_group, parent=True))
        for role, control in result.controls.items():
            self.assertTrue(cmds.listRelatives(control, shapes=True, type="nurbsCurve"), role)
            self.assertEqual(cmds.getAttr(f"{control}.translate")[0], (0.0, 0.0, 0.0))
            self.assertEqual(cmds.getAttr(f"{control}.rotate")[0], (0.0, 0.0, 0.0))
        self.assertEqual(
            {
                joint: tuple(cmds.getAttr(f"{joint}.worldMatrix[0]"))
                for joint in joints
            },
            matrices_before,
        )
        self.assertEqual(sorted(cmds.cycleCheck(all=True, list=True) or []), cycles_before)

        nodes_before_second_build = set(cmds.ls(long=True) or [])
        second = build_mmd_control_rig(root)
        self.assertFalse(second.created)
        self.assertEqual(second.controls, result.controls)
        self.assertEqual(set(cmds.ls(long=True) or []), nodes_before_second_build)

        scene_path = self.get_temp_filename("mmd_control_rig_reopen.ma")
        cmds.file(rename=scene_path)
        cmds.file(save=True, type="mayaAscii", force=True)
        cmds.file(scene_path, open=True, force=True)
        reopened_root = (cmds.ls(root, long=True) or [root])[0]
        reopened = build_mmd_control_rig(reopened_root)
        self.assertFalse(reopened.created)
        self.assertEqual(set(reopened.controls), set(result.controls))
        self.assertTrue(read_mmd_control_rig_metadata(reopened_root))

        self.assertTrue(remove_mmd_control_rig(reopened_root))
        self.assertFalse(cmds.objExists(reopened.control_group))
        self.assertFalse(
            cmds.attributeQuery(
                ATTR_MMD_CONTROL_RIG_JSON,
                node=reopened_root,
                exists=True,
            )
        )
        self.assertFalse(remove_mmd_control_rig(reopened_root))

    def test_existing_vmd_edit_and_bake_preserve_world_and_anim_curves(self):
        root = self._import_fixture()
        self.assertTrue(
            import_mmd_file(
                _VMD_PATH,
                options={"target_model": root, "pmx_path": _PMX_PATH},
            )
        )
        result = build_mmd_control_rig(root)
        metadata = read_mmd_control_rig_metadata(root)
        role, target, source = self._first_animated_control_binding(metadata)
        channel = target.rsplit(".", 1)[-1]
        original_curve = source.split(".", 1)[0]
        original_curve_uuid = cmds.ls(original_curve, uuid=True)[0]
        frames = (0, 10, 20, 30)
        before = self._capture_indexed_world_matrices(root, frames)
        control_world_before = tuple(
            cmds.getAttr(f"{result.controls[role]}.worldMatrix[0]")
        )

        edit = enter_mmd_control_rig_edit(root)

        self.assertEqual(edit["state"], "EDIT")
        self.assertEqual(before, self._capture_indexed_world_matrices(root, frames))
        self.assertEqual(
            control_world_before,
            tuple(cmds.getAttr(f"{result.controls[role]}.worldMatrix[0]")),
        )
        self.assertTrue(cmds.ls(original_curve_uuid, long=True))
        self.assertTrue(
            cmds.isConnected(
                source,
                f"{result.controls[role]}.{channel}",
            )
        )
        self.assertFalse(cmds.cycleCheck(all=True, list=True) or [])

        baked = bake_mmd_control_rig(root)

        self.assertEqual(baked["state"], "BAKED")
        self.assertEqual(before, self._capture_indexed_world_matrices(root, frames))
        self.assertTrue(cmds.ls(original_curve_uuid, long=True))
        self.assertTrue(cmds.isConnected(source, target))

    def _first_animated_control_binding(self, metadata):
        for role, binding in metadata["bindings"].items():
            for compound in binding["authoredPlugs"]:
                channels = (
                    [f"{compound}{axis}" for axis in "XYZ"]
                    if compound.endswith((".translate", ".rotate"))
                    else [compound]
                )
                for target in channels:
                    sources = cmds.listConnections(
                        target, source=True, destination=False, plugs=True
                    ) or []
                    if sources:
                        return role, target, str(sources[0])
        self.fail("fixture VMD did not create an animated control-rig binding")

    def _capture_indexed_world_matrices(self, root, frames):
        joints = [
            joint
            for joint in cmds.listRelatives(
                root, allDescendents=True, type="joint", fullPath=True
            )
            or []
            if cmds.attributeQuery(ATTR_MMD_BONE_INDEX, node=joint, exists=True)
        ]
        result = {}
        restore = cmds.currentTime(query=True)
        try:
            for frame in frames:
                cmds.currentTime(frame, edit=True)
                for joint in joints:
                    index = int(cmds.getAttr(f"{joint}.{ATTR_MMD_BONE_INDEX}"))
                    result[(index, frame)] = tuple(
                        round(float(value), 8)
                        for value in cmds.getAttr(f"{joint}.worldMatrix[0]")
                    )
        finally:
            cmds.currentTime(restore, edit=True)
        return result

    def test_remove_fails_closed_when_user_node_is_parented_under_control_group(self):
        root = self._import_fixture()
        result = build_mmd_control_rig(root)
        user_node = cmds.createNode(
            "transform",
            name="user_authored_control_note",
            parent=result.control_group,
        )

        with self.assertRaisesRegex(MmdControlRigBuildError, "topology changed"):
            remove_mmd_control_rig(root)

        self.assertTrue(cmds.objExists(user_node))
        cmds.delete(user_node)
        self.assertTrue(remove_mmd_control_rig(root))

    def test_remove_rejects_reparented_owned_control(self):
        root = self._import_fixture()
        result = build_mmd_control_rig(root)
        control = result.controls["center"]
        control_uuid = cmds.ls(control, uuid=True)[0]
        zero = result.zero_groups["center"]
        cmds.parent(control, world=True)

        with self.assertRaisesRegex(MmdControlRigBuildError, "topology changed"):
            remove_mmd_control_rig(root)

        moved_control = cmds.ls(control_uuid, long=True)[0]
        self.assertTrue(cmds.objExists(moved_control))
        cmds.parent(moved_control, zero)
        self.assertTrue(remove_mmd_control_rig(root))

    def test_duplicated_model_metadata_cannot_delete_original_controls(self):
        root = self._import_fixture()
        result = build_mmd_control_rig(root)
        duplicate = cmds.duplicate(root, returnRootsOnly=True)[0]

        with self.assertRaisesRegex(MmdControlRigBuildError, "model UUID mismatch"):
            remove_mmd_control_rig(duplicate)

        self.assertTrue(cmds.objExists(result.control_group))
        self.assertTrue(read_mmd_control_rig_metadata(root))

    def test_build_and_remove_are_single_undo_steps(self):
        root = self._import_fixture()
        result = build_mmd_control_rig(root)
        control_group_uuid = cmds.ls(result.control_group, uuid=True)[0]

        cmds.undo()
        self.assertFalse(cmds.ls(control_group_uuid, long=True))
        self.assertFalse(
            cmds.attributeQuery(ATTR_MMD_CONTROL_RIG_JSON, node=root, exists=True)
        )
        cmds.redo()
        restored = build_mmd_control_rig(root)
        self.assertFalse(restored.created)

        self.assertTrue(remove_mmd_control_rig(root))
        self.assertFalse(cmds.objExists(restored.control_group))
        cmds.undo()
        restored_after_remove = build_mmd_control_rig(root)
        self.assertFalse(restored_after_remove.created)
        self.assertTrue(cmds.objExists(restored_after_remove.control_group))

    def test_build_failure_rolls_back_nodes_and_metadata(self):
        root = self._import_fixture()
        nodes_before = set(cmds.ls(long=True) or [])

        with mock.patch.object(
            cmds,
            "curve",
            side_effect=RuntimeError("simulated curve creation failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "simulated curve"):
                build_mmd_control_rig(root)

        self.assertEqual(set(cmds.ls(long=True) or []), nodes_before)
        self.assertFalse(
            cmds.attributeQuery(ATTR_MMD_CONTROL_RIG_JSON, node=root, exists=True)
        )

    def test_parent_failure_does_not_leave_an_unowned_curve(self):
        root = self._import_fixture()
        nodes_before = set(cmds.ls(long=True) or [])

        with mock.patch.object(
            cmds,
            "parent",
            side_effect=RuntimeError("simulated parent failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "simulated parent"):
                build_mmd_control_rig(root)

        self.assertEqual(set(cmds.ls(long=True) or []), nodes_before)
        self.assertFalse(
            cmds.attributeQuery(ATTR_MMD_CONTROL_RIG_JSON, node=root, exists=True)
        )

    def test_multiple_namespaced_models_receive_separate_control_groups(self):
        root_a = self._import_fixture(use_namespace=True)
        root_b = self._import_fixture(use_namespace=True)

        rig_a = build_mmd_control_rig(root_a)
        rig_b = build_mmd_control_rig(root_b)

        self.assertNotEqual(rig_a.model_root, rig_b.model_root)
        self.assertNotEqual(rig_a.control_group, rig_b.control_group)
        self.assertTrue(cmds.objExists(rig_a.control_group))
        self.assertTrue(cmds.objExists(rig_b.control_group))
        self.assertEqual(set(rig_a.controls), set(rig_b.controls))


if __name__ == "__main__":
    import unittest

    unittest.main()
