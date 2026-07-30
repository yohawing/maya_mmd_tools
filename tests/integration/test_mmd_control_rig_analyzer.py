"""Maya integration coverage for the report-only MMD control-rig analyzer."""

from dataclasses import replace
import json
import os
from pathlib import Path
from unittest import mock

import maya.api.OpenMaya as om
from maya import cmds

from mmd_tools.core import settings
from mmd_tools.core.constants import ATTR_MMD_BONE_INDEX, ATTR_MMD_CONTROL_RIG_JSON
from mmd_tools.core.mmd_control_rig_builder import (
    CONTROL_RIG_CONTROL_OWNED,
    CONTROL_RIG_MMD_OWNED,
    MmdControlRigBuildError,
    build_mmd_control_rig,
    inspect_mmd_control_rig,
    read_mmd_control_rig_metadata,
    remove_mmd_control_rig,
)
from mmd_tools.core.mmd_control_rig_motion import (
    ROUTE_SAMPLED,
    _commit_control_rotation_group,
    _euler_degrees_from_quaternion,
    _quaternion_from_euler_degrees,
    bake_mmd_control_rig,
    control_rig_edit_routes_for_joints,
    enter_mmd_control_rig_edit,
    restore_mmd_control_rig_attached,
)
from mmd_tools.core.mmd_control_rig_analyzer import (
    INPUT_APPEND_BASE,
    INPUT_DIRECT_CHANNEL,
    INPUT_IK_CONTROLLER,
    INPUT_IK_LINK_INPUT,
    INPUT_SOLVER_OUTPUT,
    MmdControlRigBoneBinding,
    STATUS_FALLBACK,
    STATUS_MISSING,
    STATUS_READY,
    analyze_mmd_control_rig,
)
from mmd_tools.converters.bone_morph_runtime import build_bone_morph_graph
from mmd_tools.converters.vmd_scene_collector import VmdSceneCollector
from mmd_tools.converters.vmd_ik_enabled_animation import collect_ik_nodes_by_bone_name
from mmd_tools.core.vmd_data import VmdData
from mmd_tools.io.mmd_importer import import_mmd_file
from mmd_tools.io.vmd_exporter import VmdExporter
from tests.common.maya_test_base import MayaTestBase


_TEST_DATA = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
)
_PMX_PATH = os.path.join(_TEST_DATA, "mmt_test_model.pmx")
_VMD_PATH = os.path.join(_TEST_DATA, "mmt_test_model_test_motion.vmd")
_CONTROL_POLICY_TEST_CHANNELS = (
    "translateX",
    "translateY",
    "translateZ",
    "rotateX",
    "rotateY",
    "rotateZ",
    "scaleX",
    "scaleY",
    "scaleZ",
    "visibility",
)


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

    def _channel_flags(self, control):
        """Capture exact channel state for a UUID-resolved control."""
        return {
            channel: (
                bool(cmds.getAttr(f"{control}.{channel}", lock=True)),
                bool(cmds.getAttr(f"{control}.{channel}", keyable=True)),
                bool(cmds.getAttr(f"{control}.{channel}", channelBox=True)),
            )
            for channel in _CONTROL_POLICY_TEST_CHANNELS
        }

    def _metadata_control(self, metadata, role):
        """Resolve a recorded control UUID instead of trusting its display name."""
        control_uuid = metadata["controls"][role]
        controls = cmds.ls(control_uuid, long=True) or []
        self.assertEqual(len(controls), 1, role)
        return str(controls[0])

    def _assert_representative_channel_policy(self, center_flags, fk_flags):
        for channel in ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ"):
            self.assertFalse(center_flags[channel][0], channel)
            self.assertTrue(center_flags[channel][1], channel)
        for channel in ("scaleX", "scaleY", "scaleZ", "visibility"):
            self.assertTrue(center_flags[channel][0], channel)
            self.assertFalse(center_flags[channel][1], channel)
            self.assertFalse(center_flags[channel][2], channel)
        for channel in ("translateX", "translateY", "translateZ"):
            self.assertTrue(fk_flags[channel][0], channel)
            self.assertFalse(fk_flags[channel][1], channel)
            self.assertFalse(fk_flags[channel][2], channel)
        for channel in ("rotateX", "rotateY", "rotateZ"):
            self.assertFalse(fk_flags[channel][0], channel)
            self.assertTrue(fk_flags[channel][1], channel)
        for channel in ("scaleX", "scaleY", "scaleZ", "visibility"):
            self.assertTrue(fk_flags[channel][0], channel)
            self.assertFalse(fk_flags[channel][1], channel)
            self.assertFalse(fk_flags[channel][2], channel)

    def _create_minimal_control_rig_graph(self, *, include_append=False):
        """Create a small indexed Maya graph without relying on a PMX fixture."""
        root = cmds.group(empty=True, name="minimal_control_rig_model")

        def create_bone(index, mmd_name):
            joint = cmds.createNode("joint", name=f"minimal_bone_{index}", parent=root)
            cmds.addAttr(joint, longName="mmd_bone_index", attributeType="long")
            cmds.addAttr(joint, longName="mmd_bone_name", dataType="string")
            cmds.addAttr(joint, longName="mmd_bone_flags", attributeType="long")
            cmds.setAttr(f"{joint}.mmd_bone_index", index)
            cmds.setAttr(f"{joint}.mmd_bone_name", mmd_name, type="string")
            cmds.setAttr(f"{joint}.mmd_bone_flags", 0)
            return joint

        center = create_bone(0, "センター")
        left_ik = create_bone(1, "左足ＩＫ")
        right_ik = create_bone(2, "右足IK")
        left_link = create_bone(3, "__left_ik_link__")
        right_link = create_bone(4, "__right_ik_link__")
        append_joint = create_bone(5, "上半身") if include_append else None

        for side, joint, link, mmd_name in (
            ("left", left_ik, left_link, "左足ＩＫ"),
            ("right", right_ik, right_link, "右足IK"),
        ):
            solver = cmds.createNode("mmdCcdIk", name=f"minimal_{side}_mmdCcdIk")
            cmds.addAttr(solver, longName="mmd_ik_bone_name", dataType="string")
            cmds.setAttr(f"{solver}.mmd_ik_bone_name", mmd_name, type="string")
            cmds.connectAttr(
                f"{solver}.outputRotate[0]",
                f"{link}.rotate",
                force=True,
            )

        append_node = None
        if append_joint is not None:
            append_node = cmds.createNode("mmdAppend", name="minimal_mmdAppend")
            cmds.setAttr(f"{append_node}.affectRotation", True)
            cmds.setAttr(f"{append_node}.ratio", -0.5)
            cmds.setAttr(f"{append_node}.sourceRotate", 30.0, 0.0, 0.0, type="double3")
            cmds.connectAttr(
                f"{append_node}.outputRotate",
                f"{append_joint}.rotate",
                force=True,
            )

        # Keep the parent transform in the helper's return value so callers can
        # inspect the same graph through the analyzer and builder APIs.
        return root, center, left_ik, right_ik, append_joint, append_node

    def _create_bone_morph_metadata(self, root, name, morph_index, offsets):
        """Create the small network metadata consumed by the real runtime builder."""
        node = cmds.createNode("network", name=name)
        cmds.addAttr(node, longName="weight", attributeType="double", keyable=True)
        cmds.addAttr(node, longName="mmd_morph_type", dataType="string")
        cmds.addAttr(node, longName="mmd_morph_index", attributeType="long")
        cmds.addAttr(node, longName="mmd_bone_morph_offsets_json", dataType="string")
        cmds.addAttr(node, longName="mmd_model_root", attributeType="message")
        cmds.setAttr(f"{node}.mmd_morph_type", "bone", type="string")
        cmds.setAttr(f"{node}.mmd_morph_index", morph_index)
        cmds.setAttr(
            f"{node}.mmd_bone_morph_offsets_json",
            json.dumps(offsets, separators=(",", ":")),
            type="string",
        )
        cmds.connectAttr(f"{root}.message", f"{node}.mmd_model_root")
        return node

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

    def test_minimal_graph_omits_missing_semistandard_controls_without_blocker(self):
        """Optional role absence must not prevent the core rig from building."""
        root, _center, _left_ik, _right_ik, _append_joint, _append_node = (
            self._create_minimal_control_rig_graph()
        )

        spec = analyze_mmd_control_rig(root)
        roles = spec.roles_by_name
        for role in (
            "waist",
            "left_foot_ik_parent",
            "right_foot_ik_parent",
            "left_toe_ik",
            "right_toe_ik",
            "upper_body",
            "left_arm",
            "left_leg",
        ):
            with self.subTest(role=role):
                self.assertEqual(roles[role].status, STATUS_MISSING)
                self.assertFalse(roles[role].blockers)

        self.assertFalse(spec.blockers)
        self.assertTrue(spec.can_build_mvp)
        rig = build_mmd_control_rig(root, spec=spec)
        self.assertIn("center", rig.controls)
        self.assertIn("left_foot_ik", rig.controls)
        self.assertNotIn("waist", rig.controls)
        self.assertNotIn("left_toe_ik", rig.controls)

    def test_new_controls_expose_only_role_authoring_channels(self):
        """Build applies rotate-only FK and locks non-authored channels."""
        root, _center, _left_ik, _right_ik, _append_joint, _append_node = (
            self._create_minimal_control_rig_graph(include_append=True)
        )

        rig = build_mmd_control_rig(root)
        center = rig.controls["center"]
        upper_body = rig.controls["upper_body"]

        for channel in ("translateX", "rotateX"):
            with self.subTest(control="center", channel=channel):
                self.assertFalse(cmds.getAttr(f"{center}.{channel}", lock=True))
                self.assertTrue(cmds.getAttr(f"{center}.{channel}", keyable=True))
                self.assertFalse(cmds.getAttr(f"{center}.{channel}", channelBox=True))

        self.assertTrue(cmds.getAttr(f"{upper_body}.translateX", lock=True))
        self.assertFalse(cmds.getAttr(f"{upper_body}.translateX", keyable=True))
        self.assertFalse(cmds.getAttr(f"{upper_body}.translateX", channelBox=True))
        self.assertFalse(cmds.getAttr(f"{upper_body}.rotateX", lock=True))
        self.assertTrue(cmds.getAttr(f"{upper_body}.rotateX", keyable=True))
        self.assertFalse(cmds.getAttr(f"{upper_body}.rotateX", channelBox=True))

        for control in (center, upper_body):
            for channel in ("scaleX", "visibility"):
                with self.subTest(control=control, channel=channel):
                    self.assertTrue(cmds.getAttr(f"{control}.{channel}", lock=True))
                    self.assertFalse(cmds.getAttr(f"{control}.{channel}", keyable=True))
                    self.assertFalse(cmds.getAttr(f"{control}.{channel}", channelBox=True))

    def test_existing_rig_channel_policy_migration_is_safe_and_idempotent(self):
        """Build reuse repairs legacy flags but does not mutate an EDIT rig."""
        root, _center, _left_ik, _right_ik, _append_joint, _append_node = (
            self._create_minimal_control_rig_graph(include_append=True)
        )
        rig = build_mmd_control_rig(root)
        upper_body = rig.controls["upper_body"]
        translate = f"{upper_body}.translateX"
        scale = f"{upper_body}.scaleX"

        for plug in (translate, scale):
            cmds.setAttr(plug, lock=False)
            cmds.setAttr(plug, keyable=True)
        migrated = build_mmd_control_rig(root)
        self.assertFalse(migrated.created)
        self.assertTrue(cmds.getAttr(translate, lock=True))
        self.assertFalse(cmds.getAttr(translate, keyable=True))
        self.assertTrue(cmds.getAttr(scale, lock=True))
        self.assertFalse(cmds.getAttr(scale, keyable=True))

        state = tuple(
            (cmds.getAttr(plug, lock=True), cmds.getAttr(plug, keyable=True))
            for plug in (translate, scale)
        )
        build_mmd_control_rig(root)
        self.assertEqual(
            state,
            tuple(
                (cmds.getAttr(plug, lock=True), cmds.getAttr(plug, keyable=True))
                for plug in (translate, scale)
            ),
        )

        enter_mmd_control_rig_edit(root)
        cmds.setAttr(scale, lock=False)
        cmds.setAttr(scale, keyable=True)
        build_mmd_control_rig(root)
        self.assertFalse(cmds.getAttr(scale, lock=True))
        self.assertTrue(cmds.getAttr(scale, keyable=True))

    def test_control_basis_metadata_persists_and_fallback_alias_inherits(self):
        """Static basis metadata survives reopen without changing the rig DAG."""
        root, _center, _left_ik, _right_ik, _append_joint, _append_node = (
            self._create_minimal_control_rig_graph(include_append=True)
        )
        result = build_mmd_control_rig(root)
        metadata = read_mmd_control_rig_metadata(root)
        basis = metadata["authoringBases"]

        self.assertEqual(basis["center"], basis["groove"])
        self.assertEqual(basis["center"]["source"], "identity")
        self.assertEqual(basis["upper_body"]["source"], "identity")
        self.assertEqual(len(basis["center"]["quaternion"]), 4)
        self.assertIsNotNone(inspect_mmd_control_rig(root))

        scene_path = self.get_temp_filename("mmd_control_rig_basis_reopen.ma")
        cmds.file(rename=scene_path)
        cmds.file(save=True, type="mayaAscii", force=True)
        cmds.file(scene_path, open=True, force=True)
        reopened_root = (cmds.ls(root, long=True) or [root])[0]
        reopened_metadata = read_mmd_control_rig_metadata(reopened_root)
        self.assertEqual(reopened_metadata["authoringBases"], basis)
        self.assertEqual(
            reopened_metadata["authoringBases"]["center"],
            reopened_metadata["authoringBases"]["groove"],
        )
        self.assertEqual(
            (cmds.ls(reopened_metadata["controls"]["center"], long=True) or [None])[0],
            result.controls["center"],
        )

        cmds.file(new=True, force=True)
        fixture_root = self._import_fixture()
        build_mmd_control_rig(fixture_root)
        fixture_basis = read_mmd_control_rig_metadata(fixture_root)["authoringBases"]
        self.assertIn("pmx_tail", {record["source"] for record in fixture_basis.values()})

    def test_arm_controls_use_mirrored_depth_axes_and_twists_aim_at_children(self):
        """The checked-in A-pose fixture keeps ergonomic arm axes end to end."""
        root = self._import_fixture()
        spec = analyze_mmd_control_rig(root)
        rig = build_mmd_control_rig(root, spec=spec)
        joints = {
            role.role: role.binding.joint
            for role in spec.roles
            if role.binding is not None
        }
        next_roles = {
            "left_shoulder": "left_arm",
            "left_arm": "left_arm_twist",
            "left_arm_twist": "left_elbow",
            "left_elbow": "left_wrist_twist",
            "left_wrist_twist": "left_wrist",
            "right_shoulder": "right_arm",
            "right_arm": "right_arm_twist",
            "right_arm_twist": "right_elbow",
            "right_elbow": "right_wrist_twist",
            "right_wrist_twist": "right_wrist",
        }

        for role, next_role in next_roles.items():
            matrix = cmds.xform(
                rig.aim_spaces[role],
                query=True,
                worldSpace=True,
                matrix=True,
            )
            x_axis = om.MVector(*matrix[0:3]).normal()
            z_axis = om.MVector(*matrix[8:11]).normal()
            source = om.MVector(
                *cmds.xform(
                    joints[role],
                    query=True,
                    worldSpace=True,
                    translation=True,
                )
            )
            target = om.MVector(
                *cmds.xform(
                    joints[next_role],
                    query=True,
                    worldSpace=True,
                    translation=True,
                )
            )
            child_direction = (target - source).normal()
            expected_depth_sign = -1.0 if role.startswith("left_") else 1.0

            with self.subTest(role=role):
                self.assertGreater(z_axis * child_direction, 0.9998)
                self.assertGreater(x_axis.z * expected_depth_sign, 0.99)

    def test_fixed_axis_twists_expose_only_roll_across_edit_restore(self):
        """Fixed-axis twist controls keep XYZ routing but lock artist X/Y."""
        root = self._import_fixture()
        rig = build_mmd_control_rig(root)

        for role in ("left_arm_twist", "right_wrist_twist"):
            control = rig.controls[role]
            with self.subTest(role=role, state="attached"):
                for axis in "XY":
                    self.assertTrue(cmds.getAttr(f"{control}.rotate{axis}", lock=True))
                    self.assertFalse(cmds.getAttr(f"{control}.rotate{axis}", keyable=True))
                self.assertFalse(cmds.getAttr(f"{control}.rotateZ", lock=True))
                self.assertTrue(cmds.getAttr(f"{control}.rotateZ", keyable=True))

        edit = enter_mmd_control_rig_edit(root)
        self.assertEqual(edit["state"], "EDIT")
        left_twist = rig.controls["left_arm_twist"]
        cmds.undo()
        self.assertEqual(read_mmd_control_rig_metadata(root)["state"], "ATTACHED")
        self.assertTrue(cmds.getAttr(f"{left_twist}.rotateX", lock=True))
        self.assertTrue(cmds.getAttr(f"{left_twist}.rotateY", lock=True))
        cmds.redo()
        self.assertEqual(read_mmd_control_rig_metadata(root)["state"], "EDIT")
        self.assertTrue(cmds.getAttr(f"{left_twist}.rotateX", lock=True))
        self.assertTrue(cmds.getAttr(f"{left_twist}.rotateY", lock=True))
        with self.assertRaises(RuntimeError):
            cmds.setAttr(f"{left_twist}.rotateX", 5.0)
        cmds.setAttr(f"{left_twist}.rotateZ", 5.0)
        self.assertAlmostEqual(cmds.getAttr(f"{left_twist}.rotateZ"), 5.0)

        restored = restore_mmd_control_rig_attached(root)
        self.assertEqual(restored["state"], "ATTACHED")
        self.assertTrue(cmds.getAttr(f"{left_twist}.rotateX", lock=True))
        self.assertTrue(cmds.getAttr(f"{left_twist}.rotateY", lock=True))

        enter_mmd_control_rig_edit(root)
        cmds.setAttr(f"{left_twist}.rotateZ", 7.0)
        baked = bake_mmd_control_rig(root)
        self.assertEqual(baked["state"], "BAKED")
        self.assertTrue(cmds.getAttr(f"{left_twist}.rotateX", lock=True))
        self.assertTrue(cmds.getAttr(f"{left_twist}.rotateY", lock=True))
        cmds.undo()
        self.assertEqual(read_mmd_control_rig_metadata(root)["state"], "EDIT")
        self.assertTrue(cmds.getAttr(f"{left_twist}.rotateX", lock=True))
        self.assertTrue(cmds.getAttr(f"{left_twist}.rotateY", lock=True))
        cmds.redo()
        self.assertEqual(read_mmd_control_rig_metadata(root)["state"], "BAKED")
        self.assertTrue(cmds.getAttr(f"{left_twist}.rotateX", lock=True))
        self.assertTrue(cmds.getAttr(f"{left_twist}.rotateY", lock=True))

    def test_negative_append_ratio_preserves_signed_control_route(self):
        """Negative Append contribution remains authored through the base input."""
        root, _center, _left_ik, _right_ik, append_joint, append_node = (
            self._create_minimal_control_rig_graph(include_append=True)
        )
        self.assertTrue(append_joint)
        self.assertTrue(append_node)

        cmds.setAttr(f"{append_node}.ratio", 0.5)
        positive = float(cmds.getAttr(f"{append_node}.outputRotateX"))
        cmds.setAttr(f"{append_node}.ratio", -0.5)
        negative = float(cmds.getAttr(f"{append_node}.outputRotateX"))
        self.assertGreater(abs(positive), 1.0)
        self.assertLess(positive * negative, 0.0)
        self.assertAlmostEqual(abs(positive), abs(negative), places=5)

        spec = analyze_mmd_control_rig(root)
        append_binding = spec.roles_by_name["upper_body"].binding
        self.assertIsNotNone(append_binding)
        self.assertEqual(append_binding.input_kind, INPUT_APPEND_BASE)
        self.assertEqual(append_binding.authored_plugs, (f"{append_node}.baseRotate",))
        self.assertFalse(spec.blockers)

        cycles_before = sorted(cmds.cycleCheck(all=True, list=True) or [])
        rig = build_mmd_control_rig(root, spec=spec)
        edit = enter_mmd_control_rig_edit(root)
        self.assertEqual(edit["state"], "EDIT")
        self.assertAlmostEqual(cmds.getAttr(f"{append_node}.ratio"), -0.5, places=7)
        control = rig.controls["upper_body"]
        self.assertTrue(
            cmds.isConnected(
                f"{control}.rotateX",
                f"{append_node}.baseRotateX",
            )
        )
        cmds.setAttr(f"{control}.rotateX", 8.0)
        self.assertAlmostEqual(cmds.getAttr(f"{append_node}.baseRotateX"), 8.0, places=6)
        self.assertEqual(sorted(cmds.cycleCheck(all=True, list=True) or []), cycles_before)

        baked = bake_mmd_control_rig(root)
        self.assertEqual(baked["state"], "BAKED")
        self.assertAlmostEqual(cmds.getAttr(f"{append_node}.ratio"), -0.5, places=7)
        self.assertFalse(
            cmds.isConnected(
                f"{control}.rotateX",
                f"{append_node}.baseRotateX",
            )
        )

    def test_control_channel_flags_survive_lifecycle_and_failed_transition(self):
        """Ownership transitions preserve the authored channel contract."""
        root, _center, _left_ik, _right_ik, _append_joint, _append_node = (
            self._create_minimal_control_rig_graph(include_append=True)
        )
        build_mmd_control_rig(root)
        metadata = read_mmd_control_rig_metadata(root)
        center = self._metadata_control(metadata, "center")
        fk = self._metadata_control(metadata, "upper_body")
        center_flags = self._channel_flags(center)
        fk_flags = self._channel_flags(fk)
        self._assert_representative_channel_policy(center_flags, fk_flags)

        # A failure inside the existing ownership transaction must leave the
        # channel flags untouched along with its graph/metadata rollback.
        connect_attr = cmds.connectAttr
        failures = [RuntimeError("simulated lifecycle enter failure")]

        def fail_once(*args, **kwargs):
            if failures:
                raise failures.pop()
            return connect_attr(*args, **kwargs)

        with mock.patch.object(cmds, "connectAttr", side_effect=fail_once):
            with self.assertRaisesRegex(RuntimeError, "simulated lifecycle enter failure"):
                enter_mmd_control_rig_edit(root)
        self.assertEqual(read_mmd_control_rig_metadata(root)["state"], "ATTACHED")
        self.assertEqual(self._channel_flags(center), center_flags)
        self.assertEqual(self._channel_flags(fk), fk_flags)

        entered = enter_mmd_control_rig_edit(root)
        self.assertEqual(entered["state"], "EDIT")
        self.assertEqual(self._channel_flags(center), center_flags)
        self.assertEqual(self._channel_flags(fk), fk_flags)

        baked = bake_mmd_control_rig(root)
        self.assertEqual(baked["state"], "BAKED")
        self.assertEqual(self._channel_flags(center), center_flags)
        self.assertEqual(self._channel_flags(fk), fk_flags)

        entered_again = enter_mmd_control_rig_edit(root)
        self.assertEqual(entered_again["state"], "EDIT")
        restored = restore_mmd_control_rig_attached(root)
        self.assertEqual(restored["state"], "ATTACHED")
        self.assertEqual(self._channel_flags(center), center_flags)
        self.assertEqual(self._channel_flags(fk), fk_flags)

    def test_control_channel_flags_survive_build_undo_redo_and_reopen(self):
        """Channel flags survive Maya lifecycle persistence and UUID lookup."""
        root, _center, _left_ik, _right_ik, _append_joint, _append_node = (
            self._create_minimal_control_rig_graph(include_append=True)
        )
        build_mmd_control_rig(root)
        metadata = read_mmd_control_rig_metadata(root)
        center = self._metadata_control(metadata, "center")
        fk = self._metadata_control(metadata, "upper_body")
        expected_center = self._channel_flags(center)
        expected_fk = self._channel_flags(fk)

        cmds.undo()
        self.assertFalse(cmds.ls(metadata["controlGroupUuid"], long=True))
        cmds.redo()
        redone_metadata = read_mmd_control_rig_metadata(root)
        redone_center = self._metadata_control(redone_metadata, "center")
        redone_fk = self._metadata_control(redone_metadata, "upper_body")
        self.assertEqual(self._channel_flags(redone_center), expected_center)
        self.assertEqual(self._channel_flags(redone_fk), expected_fk)

        scene_path = self.get_temp_filename("mmd_control_rig_channel_policy_reopen.ma")
        cmds.file(rename=scene_path)
        cmds.file(save=True, type="mayaAscii", force=True)
        cmds.file(scene_path, open=True, force=True)
        reopened_root = (cmds.ls(root, long=True) or [root])[0]
        reopened_metadata = read_mmd_control_rig_metadata(reopened_root)
        reopened_center = self._metadata_control(reopened_metadata, "center")
        reopened_fk = self._metadata_control(reopened_metadata, "upper_body")
        self.assertEqual(self._channel_flags(reopened_center), expected_center)
        self.assertEqual(self._channel_flags(reopened_fk), expected_fk)

        # Reuse invokes the explicit migration path but resolves the same
        # controls by UUID and preserves the already-correct state.
        reused = build_mmd_control_rig(reopened_root)
        self.assertFalse(reused.created)
        self.assertEqual(
            self._channel_flags(self._metadata_control(reopened_metadata, "center")),
            expected_center,
        )
        self.assertEqual(
            self._channel_flags(self._metadata_control(reopened_metadata, "upper_body")),
            expected_fk,
        )

    def test_bone_morph_ik_controller_authors_accumulator_base_without_cycle(self):
        """The real accumulator keeps morph and IK control as one writer route."""
        try:
            probe = cmds.createNode("mmdBoneMorphAccum", name="analyzer_accum_probe")
        except RuntimeError as exc:
            self.skipTest(f"mmdBoneMorphAccum node is unavailable: {exc}")
        else:
            cmds.delete(probe)
        root, _center, left_ik, _right_ik, _append_joint, _append_node = (
            self._create_minimal_control_rig_graph()
        )
        morph = self._create_bone_morph_metadata(
            root,
            "left_ik_authoring_boneMorph",
            0,
            [
                {
                    "bone_index": 1,
                    "translation": [2.0, 0.0, 0.0],
                    "rotation": [0.0, 0.0, 0.0, 1.0],
                }
            ],
        )
        self.assertTrue(build_bone_morph_graph(root)["success"])

        spec = analyze_mmd_control_rig(root)
        binding = spec.roles_by_name["left_foot_ik"].binding
        self.assertEqual(binding.input_kind, INPUT_IK_CONTROLLER)
        accum = (cmds.ls(type="mmdBoneMorphAccum") or [None])[0]
        self.assertTrue(accum)
        self.assertEqual(
            binding.authored_plugs,
            (f"{accum}.baseRotate", f"{accum}.baseTranslate"),
        )

        rig = build_mmd_control_rig(root, spec=spec)
        enter_mmd_control_rig_edit(root)
        control = rig.controls["left_foot_ik"]
        target = next(plug for plug in binding.authored_plugs if plug.endswith(".baseTranslate"))
        target_x = f"{target}X"
        control_source = cmds.listConnections(
            target_x, source=True, destination=False, plugs=True
        ) or []
        self.assertEqual(len(control_source), 1)
        self.assertEqual(
            cmds.ls(control_source[0].split(".", 1)[0], long=True),
            cmds.ls(control, long=True),
        )
        self.assertTrue(control_source[0].endswith(".translateX"))
        self.assertEqual(
            cmds.listConnections(f"{left_ik}.translate", source=True, destination=False, plugs=True),
            [target.rsplit(".", 1)[0] + ".outputTranslate"],
        )
        cycles_before = sorted(cmds.cycleCheck(all=True, list=True) or [])
        cmds.setAttr(f"{control}.translateX", 1.5)
        cmds.setAttr(f"{morph}.weight", 0.0)
        self.assertAlmostEqual(cmds.getAttr(f"{left_ik}.translateX"), 1.5, places=5)
        cmds.setAttr(f"{morph}.weight", 1.0)
        self.assertAlmostEqual(cmds.getAttr(f"{left_ik}.translateX"), 3.5, places=5)
        self.assertEqual(sorted(cmds.cycleCheck(all=True, list=True) or []), cycles_before)

        baked = bake_mmd_control_rig(root)
        self.assertEqual(baked["state"], "BAKED")
        self.assertFalse(cmds.listConnections(target_x, source=True, destination=False, plugs=True) or [])

    def test_baked_ik_controller_base_channels_are_exported(self):
        """VMD collection retains identity-only IK controller edits after bake."""
        root, _center, _left_ik, _right_ik, _append_joint, _append_node = (
            self._create_minimal_control_rig_graph()
        )
        morph = self._create_bone_morph_metadata(
            root,
            "left_ik_export_boneMorph",
            0,
            [
                {
                    "bone_index": 1,
                    "translation": [2.0, 0.0, 0.0],
                    "rotation": [0.0, 0.0, 0.0, 1.0],
                }
            ],
        )
        self.assertTrue(build_bone_morph_graph(root)["success"])

        spec = analyze_mmd_control_rig(root)
        self.assertEqual(
            spec.roles_by_name["left_foot_ik"].binding.input_kind,
            INPUT_IK_CONTROLLER,
        )
        rig = build_mmd_control_rig(root, spec=spec)
        enter_mmd_control_rig_edit(root)
        control = rig.controls["left_foot_ik"]
        cmds.setKeyframe(control, attribute="translateX", time=3, value=0.35)
        bake_mmd_control_rig(root)
        cmds.setAttr(f"{morph}.weight", 0.0)

        collected = VmdSceneCollector().collect({"target_model": root})
        output_path = self.get_temp_filename("ik_controller_base_export.vmd")
        VmdExporter().export_vmd_animation(output_path, collected)
        parsed = VmdData().parse_file(output_path)

        left_frames = [frame for frame in parsed.bone_frames if frame.bone_name == "左足ＩＫ"]
        self.assertTrue(left_frames)
        # Maya film frame 3 maps to VMD frame 4 at 30 fps.
        frame = next(item for item in left_frames if item.frame_number == 4)
        self.assertAlmostEqual(frame.position[0], 0.35, places=5)

    def test_anim_picker_selects_owned_center_control(self):
        """Keep the Picker-to-controller selection path live in Maya."""

        from unittest.mock import patch

        from mmd_tools.adapters.maya_cmds_adapter import MayaCmdsAdapter
        from mmd_tools.ui.presenters.animation_presenter import AnimationPresenter
        from tests.integration.test_animation_presenter_e2e import _AppState, _View

        root = self._import_fixture()
        rig = build_mmd_control_rig(root)
        view = _View()
        app_state = _AppState(root=root)
        with patch(
            "mmd_tools.ui.presenters.animation_presenter.AnimationPresenter._populate_morph_groups"
        ):
            presenter = AnimationPresenter(view, app_state, maya_adapter=MayaCmdsAdapter())
        try:
            presenter.on_body_region_clicked("center")
            selected = cmds.ls(selection=True, long=True) or []
            expected = cmds.ls(rig.controls["center"], long=True) or []
            self.assertEqual(selected, expected)
            self.assertEqual(
                view.body_picker.selected_regions,
                ["center"],
                (
                    presenter._joint_for_rig_control(expected[0]),
                    presenter._bone_name_to_joint.get("センター"),
                ),
            )
        finally:
            presenter.disconnect_signals()

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
        metadata = read_mmd_control_rig_metadata(root)
        finger_roots = (
            "left_thumb_0",
            "left_index_1",
            "left_middle_1",
            "left_ring_1",
            "left_pinky_1",
            "right_thumb_0",
            "right_index_1",
            "right_middle_1",
            "right_ring_1",
            "right_pinky_1",
        )
        arm_roles = tuple(
            f"{side}_{role}"
            for side in ("left", "right")
            for role in ("shoulder", "arm", "arm_twist", "elbow", "wrist_twist", "wrist")
            if f"{side}_{role}" in result.controls
        )
        follow_roles = finger_roots + arm_roles
        self.assertEqual(len(metadata.get("helperNodes", [])), len(follow_roles))
        for role in follow_roles:
            zero = result.zero_groups[role]
            self.assertEqual(
                cmds.ls(cmds.listRelatives(zero, parent=True), long=True),
                [result.control_group],
            )
            constraints = cmds.listConnections(
                zero,
                source=True,
                destination=False,
                type="parentConstraint",
            ) or []
            self.assertEqual(len(set(constraints)), 1, role)
            targets = cmds.parentConstraint(
                constraints[0],
                query=True,
                targetList=True,
            ) or []
            joint_uuid = metadata["bindings"][role]["jointUuid"]
            joint = (cmds.ls(joint_uuid, long=True) or [None])[0]
            concrete_parent = (cmds.listRelatives(joint, parent=True, fullPath=True) or [None])[0]
            self.assertEqual(cmds.ls(targets, long=True), [concrete_parent], role)
        self.assertTrue(
            {
                "master",
                "center",
                "groove",
                "left_foot_ik",
                "right_foot_ik",
                "left_leg",
                "left_knee",
                "right_leg",
                "right_knee",
            }
            .issubset(result.controls)
        )
        self.assertEqual(
            cmds.ls(cmds.listRelatives(result.control_group, parent=True), long=True),
            [root],
        )
        self.assertEqual(result.control_group.rsplit("|", 1)[-1].rsplit(":", 1)[-1], "Controls")
        for role, control in result.controls.items():
            self.assertEqual(
                control.rsplit("|", 1)[-1].rsplit(":", 1)[-1],
                f"{role}_CTRL",
            )
        for role, zero in result.zero_groups.items():
            self.assertEqual(
                zero.rsplit("|", 1)[-1].rsplit(":", 1)[-1],
                f"{role}_ZERO",
            )
        for role, control in result.controls.items():
            self.assertTrue(cmds.listRelatives(control, shapes=True, type="nurbsCurve"), role)
            self.assertEqual(cmds.getAttr(f"{control}.translate")[0], (0.0, 0.0, 0.0))
            self.assertEqual(cmds.getAttr(f"{control}.rotate")[0], (0.0, 0.0, 0.0))
        self.assertEqual(len(cmds.listRelatives(result.controls["center"], shapes=True) or []), 4)
        self.assertEqual(len(cmds.listRelatives(result.controls["upper_body"], shapes=True) or []), 2)
        self.assertEqual(
            cmds.getAttr(f"{(cmds.listRelatives(result.controls['lower_body'], shapes=True) or [])[0]}.degree"),
            3,
        )
        self.assertEqual(
            cmds.getAttr(f"{(cmds.listRelatives(result.controls['groove'], shapes=True) or [])[0]}.degree"),
            1,
        )
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
        reopened_metadata = read_mmd_control_rig_metadata(reopened_root)
        self.assertEqual(
            len(reopened_metadata.get("helperNodes", [])),
            len(follow_roles),
        )
        helper_uuids = tuple(reopened_metadata["helperNodes"])

        self.assertTrue(remove_mmd_control_rig(reopened_root))
        self.assertFalse(cmds.objExists(reopened.control_group))
        self.assertFalse(any(cmds.ls(uuid) for uuid in helper_uuids))
        self.assertFalse(
            cmds.attributeQuery(
                ATTR_MMD_CONTROL_RIG_JSON,
                node=reopened_root,
                exists=True,
            )
        )
        self.assertFalse(remove_mmd_control_rig(reopened_root))

    def test_motion_owner_is_explicit_and_legacy_state_derives_owner(self):
        root = self._import_fixture()
        build_mmd_control_rig(root)

        metadata = read_mmd_control_rig_metadata(root)
        self.assertEqual(metadata["owner"], CONTROL_RIG_MMD_OWNED)

        # v3 scenes written before the owner field remain readable and derive
        # the MMD-side writer from their legacy ATTACHED state.
        legacy = dict(metadata)
        legacy.pop("owner")
        cmds.setAttr(
            f"{root}.{ATTR_MMD_CONTROL_RIG_JSON}",
            json.dumps(legacy, ensure_ascii=False),
            type="string",
        )
        self.assertEqual(
            read_mmd_control_rig_metadata(root)["owner"],
            CONTROL_RIG_MMD_OWNED,
        )

        entered = enter_mmd_control_rig_edit(root)
        self.assertEqual(entered["state"], "EDIT")
        self.assertEqual(entered["owner"], CONTROL_RIG_CONTROL_OWNED)
        self.assertNotEqual(entered["owner"], "CONVERTING")

        baked = bake_mmd_control_rig(root)
        self.assertEqual(baked["state"], "BAKED")
        self.assertEqual(baked["owner"], CONTROL_RIG_MMD_OWNED)

        entered = enter_mmd_control_rig_edit(root)
        restored = restore_mmd_control_rig_attached(root)
        self.assertEqual(restored["state"], "ATTACHED")
        self.assertEqual(restored["owner"], CONTROL_RIG_MMD_OWNED)

    def test_enter_edit_failure_restores_graph_values_and_raw_metadata(self):
        root = self._import_fixture()
        self.assertTrue(
            import_mmd_file(
                _VMD_PATH,
                options={"target_model": root, "pmx_path": _PMX_PATH},
            )
        )
        rig = build_mmd_control_rig(root)
        metadata_before = cmds.getAttr(f"{root}.{ATTR_MMD_CONTROL_RIG_JSON}")

        plugs = set()
        for role, binding in read_mmd_control_rig_metadata(root)["bindings"].items():
            control = rig.controls[role]
            for compound in binding["authoredPlugs"]:
                channels = (
                    [f"{compound}{axis}" for axis in "XYZ"]
                    if compound.endswith((".translate", ".rotate", ".baseTranslate", ".baseRotate"))
                    else [compound]
                )
                for target in channels:
                    raw_channel = target.rsplit(".", 1)[-1]
                    if raw_channel.startswith("baseRotate"):
                        channel = f"rotate{raw_channel[-1]}"
                    elif raw_channel.startswith("baseTranslate"):
                        channel = f"translate{raw_channel[-1]}"
                    elif raw_channel.startswith("inputRotateElement"):
                        channel = f"rotate{raw_channel[-1]}"
                    else:
                        channel = raw_channel
                    plugs.update((target, f"{control}.{channel}"))
        before = {
            plug: (
                tuple(cmds.listConnections(plug, source=True, destination=False, plugs=True) or []),
                cmds.getAttr(plug),
            )
            for plug in sorted(plugs)
            if cmds.objExists(plug)
        }

        connect_attr = cmds.connectAttr
        calls = [0]

        def fail_after_first(*args, **kwargs):
            calls[0] += 1
            if calls[0] == 2:
                raise RuntimeError("simulated enter connection failure")
            return connect_attr(*args, **kwargs)

        with mock.patch.object(cmds, "connectAttr", side_effect=fail_after_first):
            with self.assertRaisesRegex(RuntimeError, "simulated enter"):
                enter_mmd_control_rig_edit(root)

        self.assertEqual(cmds.getAttr(f"{root}.{ATTR_MMD_CONTROL_RIG_JSON}"), metadata_before)
        self.assertEqual(read_mmd_control_rig_metadata(root)["state"], "ATTACHED")
        self.assertEqual(read_mmd_control_rig_metadata(root)["owner"], CONTROL_RIG_MMD_OWNED)
        after = {
            plug: (
                tuple(cmds.listConnections(plug, source=True, destination=False, plugs=True) or []),
                cmds.getAttr(plug),
            )
            for plug in sorted(plugs)
            if cmds.objExists(plug)
        }
        self.assertEqual(after, before)

    def test_model_root_master_fallback_stays_outside_driven_hierarchy(self):
        root = (cmds.ls(self._import_fixture(), long=True) or [None])[0]
        spec = analyze_mmd_control_rig(root)
        fallback_binding = MmdControlRigBoneBinding(
            joint=root,
            mmd_name="model_root",
            bone_index=None,
            pmx_flags=0,
            input_kind=INPUT_DIRECT_CHANNEL,
            authored_plugs=(f"{root}.translate", f"{root}.rotate"),
        )
        roles = tuple(
            replace(
                role,
                status=STATUS_FALLBACK,
                binding=fallback_binding,
                fallback="model_root",
                warnings=(),
                blockers=(),
            )
            if role.role == "master"
            else role
            for role in spec.roles
        )
        fallback_spec = replace(spec, roles=roles)
        cycles_before = sorted(cmds.cycleCheck(all=True, list=True) or [])

        rig = build_mmd_control_rig(root, spec=fallback_spec)

        self.assertFalse(cmds.listRelatives(rig.control_group, parent=True) or [])
        enter_mmd_control_rig_edit(root)
        self.assertEqual(sorted(cmds.cycleCheck(all=True, list=True) or []), cycles_before)
        restored = restore_mmd_control_rig_attached(root)
        self.assertEqual(restored["state"], "ATTACHED")


    def test_leg_controls_own_pre_solver_thigh_and_knee_rotation(self):
        root = self._import_fixture()
        spec = analyze_mmd_control_rig(root)
        left_role = spec.roles_by_name["left_leg"]
        knee_role = spec.roles_by_name["left_knee"]

        self.assertEqual(left_role.status, STATUS_READY)
        self.assertEqual(left_role.binding.input_kind, INPUT_IK_LINK_INPUT)
        self.assertEqual(len(left_role.binding.authored_plugs), 3)
        self.assertEqual(knee_role.status, STATUS_READY)
        self.assertEqual(knee_role.binding.input_kind, INPUT_IK_LINK_INPUT)

        rig = build_mmd_control_rig(root)
        metadata = enter_mmd_control_rig_edit(root)
        control = rig.controls["left_leg"]
        target_x = metadata["bindings"]["left_leg"]["authoredPlugs"][0]
        solver = target_x.split(".", 1)[0]
        thigh = left_role.binding.joint
        knee_joint = knee_role.binding.joint

        control_source = (cmds.listConnections(target_x, source=True, destination=False, plugs=True) or [""])[0]
        self.assertEqual(control_source.rsplit(".", 1)[-1], "rotateX")
        self.assertEqual(cmds.ls(control_source.split(".", 1)[0], long=True)[0], control)
        self.assertTrue(
            (cmds.listConnections(f"{thigh}.rotate", source=True, destination=False, plugs=True) or [""])[0]
            .startswith(f"{solver}.outputRotate[")
        )

        goal_before = tuple(cmds.getAttr(f"{solver}.goalWorldMatrix"))
        knee_before = tuple(cmds.getAttr(f"{knee_joint}.worldMatrix[0]"))
        cmds.setAttr(f"{control}.rotateX", 7.5)
        self.assertAlmostEqual(cmds.getAttr(target_x), 7.5, places=6)
        goal_after = tuple(cmds.getAttr(f"{solver}.goalWorldMatrix"))
        knee_after = tuple(cmds.getAttr(f"{knee_joint}.worldMatrix[0]"))
        self.assertLess(max(abs(a - b) for a, b in zip(goal_before, goal_after)), 1.0e-9)
        self.assertGreater(max(abs(a - b) for a, b in zip(knee_before, knee_after)), 1.0e-4)
        baked = bake_mmd_control_rig(root)
        self.assertEqual(baked["state"], "BAKED")
        self.assertFalse(cmds.listConnections(target_x, source=True, destination=False, plugs=True) or [])
        self.assertAlmostEqual(cmds.getAttr(target_x), 7.5, places=6)

    def test_ik_disabled_leg_fk_keeps_pre_solver_single_writer_and_no_cycle(self):
        """IK OFF still authors the solver input while output ownership stays intact."""
        root = self._import_fixture()
        spec = analyze_mmd_control_rig(root)
        left_role = spec.roles_by_name["left_leg"]
        self.assertEqual(left_role.status, STATUS_READY)
        self.assertEqual(left_role.binding.input_kind, INPUT_IK_LINK_INPUT)

        rig = build_mmd_control_rig(root, spec=spec)
        metadata = enter_mmd_control_rig_edit(root)
        control = rig.controls["left_leg"]
        ik_control = rig.controls["left_foot_ik"]
        target_x = metadata["bindings"]["left_leg"]["authoredPlugs"][0]
        solver = target_x.split(".", 1)[0]
        thigh = left_role.binding.joint
        cycles_before = sorted(cmds.cycleCheck(all=True, list=True) or [])

        # EDIT exposes IK state through the owned foot-IK controller.  The
        # solver enabled plug is intentionally a downstream single-writer
        # edge, while the leg control continues to author pre-solver input.
        cmds.setAttr(f"{ik_control}.ikEnabled", False)
        target_sources = cmds.listConnections(
            target_x,
            source=True,
            destination=False,
            plugs=True,
        ) or []
        self.assertEqual(len(target_sources), 1)
        self.assertEqual(cmds.ls(target_sources[0].split(".", 1)[0], long=True)[0], control)
        thigh_sources = cmds.listConnections(
            f"{thigh}.rotate",
            source=True,
            destination=False,
            plugs=True,
        ) or []
        self.assertEqual(len(thigh_sources), 1)
        self.assertTrue(thigh_sources[0].startswith(f"{solver}.outputRotate["))
        self.assertFalse(
            cmds.listConnections(
                f"{thigh}.offsetParentMatrix",
                source=True,
                destination=False,
                plugs=True,
            )
            or []
        )

        cmds.setAttr(f"{control}.rotateX", 7.5)
        self.assertAlmostEqual(cmds.getAttr(target_x), 7.5, places=6)
        self.assertEqual(
            sorted(cmds.cycleCheck(all=True, list=True) or []),
            cycles_before,
        )
        self.assertFalse(
            cmds.listConnections(
                f"{thigh}.rotate",
                source=True,
                destination=True,
                plugs=True,
                type="transform",
            )
            or []
        )

    def test_knee_control_visibility_is_inverse_of_leg_ik_state(self):
        root = self._import_fixture()
        rig = build_mmd_control_rig(root)
        edit = enter_mmd_control_rig_edit(root)
        foot_ik = rig.controls["left_foot_ik"]
        knee = rig.controls["left_knee"]
        ik_controls = tuple(
            rig.controls[role]
            for role in ("left_foot_ik_parent", "left_foot_ik", "left_toe_ik")
            if role in rig.controls
        )
        knee_shapes = cmds.listRelatives(knee, shapes=True, fullPath=True) or []
        ik_shapes = [
            shape
            for control in ik_controls
            for shape in (cmds.listRelatives(control, shapes=True, fullPath=True) or [])
        ]
        self.assertTrue(knee_shapes)
        self.assertTrue(ik_shapes)

        cmds.setAttr(f"{foot_ik}.ikEnabled", True)
        self.assertTrue(all(bool(cmds.getAttr(f"{shape}.visibility")) for shape in ik_shapes))
        self.assertTrue(all(not bool(cmds.getAttr(f"{shape}.visibility")) for shape in knee_shapes))

        cmds.setAttr(f"{foot_ik}.ikEnabled", False)
        self.assertTrue(all(not bool(cmds.getAttr(f"{shape}.visibility")) for shape in ik_shapes))
        self.assertTrue(all(bool(cmds.getAttr(f"{shape}.visibility")) for shape in knee_shapes))
        inverter_uuids = {
            row["uuid"] for row in edit.get("ikVisibilityInverters", [])
        }
        self.assertEqual(len(inverter_uuids), 2)

        bake_mmd_control_rig(root)
        reentered = enter_mmd_control_rig_edit(root)
        self.assertEqual(
            {row["uuid"] for row in reentered.get("ikVisibilityInverters", [])},
            inverter_uuids,
        )
        bake_mmd_control_rig(root)
        self.assertTrue(remove_mmd_control_rig(root))
        self.assertTrue(
            all(not (cmds.ls(uuid, long=True) or []) for uuid in inverter_uuids)
        )

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
        control_row = next(
            row for row in edit["journal"]["channels"] if row["target"] == target
        )
        self.assertTrue(
            cmds.isConnected(
                control_row.get("controlSource") or source,
                f"{result.controls[role]}.{channel}",
            )
        )
        self.assertFalse(cmds.cycleCheck(all=True, list=True) or [])

        baked = bake_mmd_control_rig(root)

        self.assertEqual(baked["state"], "BAKED")
        self.assertEqual(before, self._capture_indexed_world_matrices(root, frames))
        self.assertTrue(cmds.ls(original_curve_uuid, long=True))
        self.assertTrue(cmds.isConnected(source, target))

    def test_sampled_bake_creates_mmd_curve_when_controller_is_only_source(self):
        """IK controller samples survive Bake when the MMD side had no curve."""
        root = self._import_fixture()
        rig = build_mmd_control_rig(root)
        entered = enter_mmd_control_rig_edit(root)
        control = rig.controls["left_foot_ik"]
        target = next(
            target
            for row in entered["journal"]["channels"]
            if row["control"].startswith(f"{control}.translateX")
            for target in [row["target"]]
        )
        source_row = next(row for row in entered["journal"]["channels"] if row["target"] == target)
        self.assertIsNone(source_row["source"])

        cmds.setKeyframe(control, attribute="translateX", time=6, value=0.2)
        cmds.setKeyframe(control, attribute="translateX", time=11, value=0.8)
        baked = bake_mmd_control_rig(root)

        self.assertEqual(baked["state"], "BAKED")
        source = cmds.listConnections(target, source=True, destination=False, plugs=True) or []
        self.assertEqual(len(source), 1)
        source_node = source[0].split(".", 1)[0]
        self.assertTrue(cmds.nodeType(source_node).startswith("animCurve"))
        self.assertEqual(
            cmds.keyframe(source_node, query=True, timeChange=True),
            list(range(6, 12)),
        )
        self.assertAlmostEqual(cmds.getAttr(target, time=6), 0.2, places=6)
        self.assertAlmostEqual(cmds.getAttr(target, time=11), 0.8, places=6)

    def test_sampled_bake_failure_removes_new_mmd_curve_and_restores_edit(self):
        """A post-sample failure must remove the transient MMD curve."""
        root = self._import_fixture()
        rig = build_mmd_control_rig(root)
        entered = enter_mmd_control_rig_edit(root)
        control = rig.controls["left_foot_ik"]
        row = next(
            row
            for row in entered["journal"]["channels"]
            if row["control"].startswith(f"{control}.translateX")
        )
        target = row["target"]
        cmds.setKeyframe(control, attribute="translateX", time=6, value=0.2)
        cmds.setKeyframe(control, attribute="translateX", time=11, value=0.8)
        curves_before = set(cmds.ls(type="animCurve") or [])

        from mmd_tools.core import mmd_control_rig_motion as motion_module

        with mock.patch.object(
            motion_module,
            "_restore_offsets",
            side_effect=RuntimeError("simulated sampled bake failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "simulated sampled bake failure"):
                bake_mmd_control_rig(root)

        self.assertEqual(read_mmd_control_rig_metadata(root)["state"], "EDIT")
        restored_source = cmds.listConnections(
            target, source=True, destination=False, plugs=True
        ) or []
        self.assertEqual(len(restored_source), 1)
        self.assertEqual(
            cmds.ls(restored_source[0].split(".", 1)[0], long=True)[0],
            row["control"].split(".", 1)[0],
        )
        self.assertEqual(set(cmds.ls(type="animCurve") or []), curves_before)

    def test_native_animcurve_has_independent_owner_representations(self):
        """Native animCurve routes retain UUID-stable MMD and control curves."""
        root = self._import_fixture()
        build_mmd_control_rig(root)
        metadata = read_mmd_control_rig_metadata(root)
        target = None
        for binding in metadata["bindings"].values():
            if binding.get("inputKind") != INPUT_DIRECT_CHANNEL:
                continue
            for compound in binding.get("authoredPlugs", []):
                candidates = (
                    [f"{compound}{axis}" for axis in "XYZ"]
                    if compound.endswith((".translate", ".rotate"))
                    else [compound]
                )
                for candidate in candidates:
                    if cmds.objExists(candidate):
                        try:
                            cmds.setKeyframe(candidate, time=0, value=0.0)
                            cmds.setKeyframe(candidate, time=10, value=5.0)
                        except RuntimeError:
                            continue
                        target = candidate
                        break
                if target:
                    break
            if target:
                break
        self.assertTrue(target)
        source = (cmds.listConnections(target, source=True, destination=False, plugs=True) or [None])[0]
        self.assertTrue(source)
        source_uuid = cmds.ls(source.split(".", 1)[0], uuid=True)[0]

        entered = enter_mmd_control_rig_edit(root)
        row = next(row for row in entered["journal"]["channels"] if row["target"] == target)
        control_source = row["controlSource"]
        self.assertTrue(control_source)
        self.assertNotEqual(source_uuid, cmds.ls(control_source.split(".", 1)[0], uuid=True)[0])
        self.assertFalse(cmds.isConnected(source, target))
        self.assertTrue(cmds.isConnected(control_source, row["control"]))

        control_uuid = cmds.ls(control_source.split(".", 1)[0], uuid=True)[0]
        renamed_source = cmds.rename(source.split(".", 1)[0], "cr061_mmd_curve_RENAMED")
        renamed_control = cmds.rename(control_source.split(".", 1)[0], "cr061_control_curve_RENAMED")
        self.assertTrue(cmds.ls(source_uuid, long=True))
        self.assertTrue(cmds.ls(control_uuid, long=True))

        baked = bake_mmd_control_rig(root)
        self.assertEqual(baked["owner"], CONTROL_RIG_MMD_OWNED)
        resolved_source = f"{(cmds.ls(source_uuid, long=True) or [renamed_source])[0]}.{source.rsplit('.', 1)[-1]}"
        resolved_control = f"{renamed_control}.{control_source.rsplit('.', 1)[-1]}"
        self.assertTrue(cmds.isConnected(resolved_source, target))
        self.assertFalse(cmds.isConnected(resolved_control, row["control"]))

        entered_again = enter_mmd_control_rig_edit(root)
        row_again = next(row for row in entered_again["journal"]["channels"] if row["target"] == target)
        self.assertEqual(control_uuid, cmds.ls(row_again["controlSource"].split(".", 1)[0], uuid=True)[0])
        target_rows = [
            row
            for row in entered_again.get("curveRepresentations", [])
            if row.get("targetRef") == row_again.get("targetRef")
        ]
        self.assertEqual(len(target_rows), 1)

    def test_native_animcurve_bake_failure_restores_curve_payload_and_metadata(self):
        """A failure after a curve copy restores payload, graph, and owner."""
        root = self._import_fixture()
        build_mmd_control_rig(root)
        metadata = read_mmd_control_rig_metadata(root)
        targets = []
        for binding in metadata["bindings"].values():
            if binding.get("inputKind") != INPUT_DIRECT_CHANNEL:
                continue
            for compound in binding.get("authoredPlugs", []):
                candidates = (
                    [f"{compound}{axis}" for axis in "XYZ"]
                    if compound.endswith((".translate", ".rotate"))
                    else [compound]
                )
                for candidate in candidates:
                    # Keep this failure-injection case on the scalar copy
                    # path.  Rotation compounds use the dedicated quaternion
                    # copier and are covered by the round-trip tests below.
                    if not candidate.rsplit(".", 1)[-1].startswith("translate"):
                        continue
                    if not cmds.objExists(candidate):
                        continue
                    candidate_node = candidate.split(".", 1)[0]
                    # Only choose identity-basis joints; joints with an
                    # authored jointOrient/parent basis intentionally use
                    # sampled conversion and do not invoke native copying.
                    if any(
                        abs(float(cmds.getAttr(f"{candidate_node}.jointOrient{axis}")))
                        > 1.0e-8
                        for axis in "XYZ"
                        if cmds.attributeQuery(
                            f"jointOrient{axis}", node=candidate_node, exists=True
                        )
                    ):
                        continue
                    # The imported fixture normally routes every authored
                    # channel through VMD_Motion.  Detach two scalar
                    # translation channels from that layer so this test
                    # exercises the native direct animCurve copy/rollback
                    # path rather than the layer-route transaction.
                    for layer in cmds.ls(type="animLayer") or []:
                        if str(layer) in {"BaseAnimation", "baseAnimation"}:
                            continue
                        for layer_attr in cmds.animLayer(
                            layer, query=True, attribute=True
                        ) or []:
                            layer_attr_text = str(layer_attr)
                            layer_attr_node, _, layer_attr_name = layer_attr_text.partition(".")
                            candidate_node, _, candidate_name = candidate.partition(".")
                            if layer_attr_text == candidate or (
                                layer_attr_node == candidate_node
                                and layer_attr_name.startswith("translate")
                                and candidate_name.startswith("translate")
                            ):
                                try:
                                    cmds.animLayer(
                                        layer,
                                        edit=True,
                                        removeAttribute=str(layer_attr),
                                    )
                                except RuntimeError:
                                    pass
                    for source in cmds.listConnections(
                        candidate,
                        source=True,
                        destination=False,
                        plugs=True,
                    ) or []:
                        try:
                            cmds.disconnectAttr(source, candidate)
                        except RuntimeError:
                            pass
                    curve = cmds.createNode("animCurveTL")
                    cmds.setKeyframe(curve, time=0, value=0.0)
                    cmds.setKeyframe(curve, time=10, value=5.0)
                    cmds.connectAttr(f"{curve}.output", candidate, force=True)
                    try:
                        cmds.setKeyframe(candidate, time=0, value=0.0)
                        cmds.setKeyframe(candidate, time=10, value=5.0)
                    except RuntimeError:
                        continue
                    targets.append(candidate)
                    break
                if len(targets) >= 2:
                    break
            if len(targets) >= 2:
                break
        self.assertGreaterEqual(len(targets), 2)
        entered = enter_mmd_control_rig_edit(root)
        rows = [row for row in entered["journal"]["channels"] if row["target"] in targets]
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(not row.get("layerRoute") for row in rows), rows)
        # Force the two synthetic scalar channels onto the explicit direct
        # route contract.  Their source curves are standalone animCurveTL
        # nodes, so this is the native copy path under test (the production
        # classifier may conservatively mark the fixture joint as sampled
        # because of its imported joint basis).
        raw_metadata = json.loads(cmds.getAttr(f"{root}.{ATTR_MMD_CONTROL_RIG_JSON}"))
        for row in raw_metadata["journal"]["channels"]:
            if row.get("target") in targets:
                row["routeClass"] = "same_basis"
                row["routeReasons"] = []
                row.pop("layerRoute", None)
        selected_rows = [
            row for row in raw_metadata["journal"]["channels"] if row.get("target") in targets
        ]
        raw_metadata["journal"]["channels"] = selected_rows
        raw_metadata["journal"]["ikEnabled"] = []
        raw_metadata["journal"]["offsetParentMatrix"] = []
        raw_metadata.pop("rotationConverters", None)
        cmds.setAttr(
            f"{root}.{ATTR_MMD_CONTROL_RIG_JSON}",
            json.dumps(raw_metadata, ensure_ascii=False, sort_keys=True),
            type="string",
        )
        before = {
            row["source"]: tuple(
                cmds.keyframe(row["source"].split(".", 1)[0], query=True, valueChange=True) or []
            )
            for row in rows
        }
        cmds.setAttr(rows[0]["control"], 17.0)
        metadata_before = cmds.getAttr(f"{root}.{ATTR_MMD_CONTROL_RIG_JSON}")
        from mmd_tools.core import mmd_control_rig_motion as motion_module

        original_copy = motion_module._copy_animation_curve
        calls = [0]
        destructive_calls = [0]
        destination_sources = {str(row["source"]) for row in rows if row.get("source")}

        def fail_after_first(cmds_module, source_plug, destination_plug):
            calls[0] += 1
            # The transaction snapshots may copy curves before the
            # destructive bake.  Count only copies whose destination is one
            # of the authored MMD curves and fail on the second body copy.
            if str(destination_plug) in destination_sources:
                destructive_calls[0] += 1
            if destructive_calls[0] == 2:
                raise RuntimeError("simulated curve copy failure")
            return original_copy(cmds_module, source_plug, destination_plug)

        with mock.patch.object(motion_module, "_copy_animation_curve", side_effect=fail_after_first):
            try:
                bake_mmd_control_rig(root)
            except RuntimeError as exc:
                self.assertRegex(str(exc), "simulated curve copy failure")
            else:
                self.fail(f"native copy hook was not invoked: calls={calls[0]} rows={rows}")
        self.assertGreaterEqual(destructive_calls[0], 2)
        self.assertEqual(cmds.getAttr(f"{root}.{ATTR_MMD_CONTROL_RIG_JSON}"), metadata_before)
        self.assertEqual(read_mmd_control_rig_metadata(root)["state"], "EDIT")
        for source, values in before.items():
            self.assertEqual(
                tuple(cmds.keyframe(source.split(".", 1)[0], query=True, valueChange=True) or []),
                values,
            )

    def test_edit_display_offset_uses_build_reference_after_scrubbing(self):
        """Controls retain the build-time FK basis when EDIT starts elsewhere."""
        root = self._import_fixture()
        self.assertTrue(
            import_mmd_file(
                _VMD_PATH,
                options={"target_model": root, "pmx_path": _PMX_PATH},
            )
        )
        cmds.currentTime(0, edit=True)
        rig = build_mmd_control_rig(root)
        metadata = read_mmd_control_rig_metadata(root)
        self.assertEqual(metadata["displayReferenceTime"], 0.0)

        direct_roles = [
            role
            for role, binding in sorted(metadata["bindings"].items())
            if binding.get("inputKind") == INPUT_DIRECT_CHANNEL
            and role not in {"left_foot_ik", "right_foot_ik"}
            and role in rig.controls
        ]
        self.assertGreaterEqual(len(direct_roles), 2)
        joints = {
            role: (cmds.ls(metadata["bindings"][role]["joint"], long=True) or [None])[0]
            for role in direct_roles
        }
        self.assertTrue(all(joints.values()))

        cmds.currentTime(20, edit=True)
        edit = enter_mmd_control_rig_edit(root)
        self.assertEqual(edit["state"], "EDIT")
        self.assertEqual(float(cmds.currentTime(query=True)), 20.0)

        def relative_matrix(role):
            control_matrix = om.MMatrix(
                cmds.getAttr(f"{rig.controls[role]}.worldMatrix[0]")
            )
            joint_matrix = om.MMatrix(
                cmds.getAttr(f"{joints[role]}.worldMatrix[0]")
            )
            return control_matrix * joint_matrix.inverse()

        cmds.currentTime(0, edit=True)
        cmds.refresh(force=True)
        reference = {role: relative_matrix(role) for role in direct_roles}
        drift = []
        for frame in (1, 3, 5, 10, 20):
            cmds.currentTime(frame, edit=True)
            cmds.refresh(force=True)
            for role in direct_roles:
                current = relative_matrix(role)
                drift.append(
                    max(
                        abs(float(current[index]) - float(reference[role][index]))
                        for index in range(16)
                    )
                )
        self.assertLess(max(drift), 1.0e-6, {"roles": direct_roles, "maxDrift": max(drift)})

    def test_sampled_direct_rotation_keeps_control_axis_during_live_edit(self):
        """JO-sampled direct XYZ still needs live Control-to-joint basis conversion."""
        root = self._import_fixture()
        rig = build_mmd_control_rig(root)
        edit = enter_mmd_control_rig_edit(root)
        candidates = {}
        for row in edit["journal"]["channels"]:
            target = str(row.get("target") or "")
            control_plug = str(row.get("control") or "")
            target_node, _, target_attr = target.partition(".")
            control_node, _, control_attr = control_plug.partition(".")
            basis = row.get("authoringBasis") or {}
            quaternion = tuple(float(value) for value in basis.get("quaternion", ()))
            if (
                row.get("routeClass") != "sampled"
                or target_attr not in {"rotateX", "rotateY", "rotateZ"}
                or control_attr != target_attr
                or len(quaternion) != 4
                or max(abs(quaternion[index]) for index in range(3)) <= 1.0e-8
            ):
                continue
            key = (control_node, target_node)
            candidates.setdefault(key, {})[target_attr] = row
        complete = [
            (nodes, rows)
            for nodes, rows in candidates.items()
            if set(rows) == {"rotateX", "rotateY", "rotateZ"}
        ]
        self.assertTrue(complete)
        (control, joint), rows = complete[0]
        self.assertIn(control, rig.controls.values())
        for axis in "XYZ":
            incoming = cmds.listConnections(
                rows[f"rotate{axis}"]["target"],
                source=True,
                destination=False,
                plugs=True,
            ) or []
            self.assertEqual(len(incoming), 1)
            self.assertEqual(cmds.nodeType(incoming[0].split(".", 1)[0]), "decomposeMatrix")

        rest_control = om.MMatrix(cmds.getAttr(f"{control}.worldMatrix[0]"))
        rest_joint = om.MMatrix(cmds.getAttr(f"{joint}.worldMatrix[0]"))
        control_x = om.MVector(
            rest_control.getElement(0, 0),
            rest_control.getElement(0, 1),
            rest_control.getElement(0, 2),
        ).normal()
        cmds.setAttr(f"{control}.rotateX", 10.0)
        after = om.MMatrix(cmds.getAttr(f"{joint}.worldMatrix[0]"))
        quaternion = om.MTransformationMatrix(rest_joint.inverse() * after).rotation(
            asQuaternion=True
        )
        axis, angle = quaternion.asAxisAngle()
        self.assertGreater(abs(float(angle)), 1.0e-4)
        self.assertGreater(abs(axis * control_x), 0.999)

        edited_world = tuple(float(value) for value in cmds.getAttr(f"{joint}.worldMatrix[0]"))
        bake_mmd_control_rig(root)
        baked_world = tuple(float(value) for value in cmds.getAttr(f"{joint}.worldMatrix[0]"))
        self.assertLess(
            max(abs(actual - expected) for actual, expected in zip(baked_world, edited_world)),
            1.0e-6,
        )

        enter_mmd_control_rig_edit(root)
        reentered_world = tuple(float(value) for value in cmds.getAttr(f"{joint}.worldMatrix[0]"))
        self.assertLess(
            max(
                abs(actual - expected)
                for actual, expected in zip(reentered_world, edited_world)
            ),
            1.0e-6,
        )

        for frame, x_value, y_value in ((0, 0.0, 0.0), (10, 35.0, 18.0)):
            cmds.setKeyframe(
                control,
                attribute="rotateX",
                time=frame,
                value=x_value,
            )
            cmds.setKeyframe(
                control,
                attribute="rotateY",
                time=frame,
                value=y_value,
            )
        frames = range(0, 11)
        edit_world = {
            frame: tuple(
                float(value)
                for value in cmds.getAttr(f"{joint}.worldMatrix[0]", time=frame)
            )
            for frame in frames
        }
        bake_mmd_control_rig(root)
        bake_errors = [
            abs(actual - expected)
            for frame in frames
            for actual, expected in zip(
                cmds.getAttr(f"{joint}.worldMatrix[0]", time=frame),
                edit_world[frame],
            )
        ]
        self.assertLess(max(bake_errors), 1.0e-5)
        baked = read_mmd_control_rig_metadata(root)
        representation = next(
            row
            for row in baked["curveRepresentations"]
            if row.get("target") == rows["rotateX"]["target"]
        )
        self.assertFalse(representation.get("quaternionInterpolation"))

    def test_ik_link_quaternion_compound_bakes_evaluated_xyz(self):
        control = cmds.createNode("transform", name="ik_link_bake_CTRL")
        solver = cmds.createNode("mmdCcdIk", name="ik_link_bake_solver")
        values = {"X": 24.0, "Y": -38.0, "Z": 17.0}
        rows = []
        sources = {}
        for axis in "XYZ":
            control_plug = f"{control}.rotate{axis}"
            target = f"{solver}.inputRotate[0].inputRotateElement{axis}"
            cmds.setKeyframe(control, attribute=f"rotate{axis}", time=0, value=0.0)
            cmds.setKeyframe(
                control,
                attribute=f"rotate{axis}",
                time=20,
                value=values[axis],
            )
            source = cmds.listConnections(
                control_plug,
                source=True,
                destination=False,
                plugs=True,
            )[0]
            cmds.connectAttr(control_plug, target)
            rows.append(
                {
                    "control": control_plug,
                    "target": target,
                    "source": None,
                    "controlSource": source,
                    "routeClass": ROUTE_SAMPLED,
                    "routeReasons": ["ik"],
                }
            )
            sources[control_plug] = source
        cmds.rotationInterpolation(
            *(f"{control}.rotate{axis}" for axis in "XYZ"),
            convert="quaternionSlerp",
        )
        frames = tuple(frame * 0.5 for frame in range(0, 41))
        before = {
            (frame, axis): float(
                cmds.getAttr(
                    f"{solver}.inputRotate[0].inputRotateElement{axis}",
                    time=frame,
                )
            )
            for frame in frames
            for axis in "XYZ"
        }

        _commit_control_rotation_group(
            cmds,
            rows,
            sources,
            quaternion_interpolation=True,
        )

        errors = [
            abs(
                float(
                    cmds.getAttr(
                        f"{solver}.inputRotate[0].inputRotateElement{axis}",
                        time=frame,
                    )
                )
                - before[(frame, axis)]
            )
            for frame in frames
            for axis in "XYZ"
        ]
        self.assertLess(max(errors), 1.0e-2)
        for axis in "XYZ":
            target = f"{solver}.inputRotate[0].inputRotateElement{axis}"
            curve = cmds.listConnections(
                target,
                source=True,
                destination=False,
                type="animCurve",
            )[0]
            self.assertEqual(
                set(cmds.keyTangent(curve, query=True, inTangentType=True) or []),
                {"linear"},
            )
            self.assertEqual(
                set(cmds.keyTangent(curve, query=True, outTangentType=True) or []),
                {"linear"},
            )

    def test_quaternion_euler_roundtrip_honors_all_maya_rotate_orders(self):
        values = (23.0, -41.0, 67.0)
        for rotate_order in range(6):
            with self.subTest(rotate_order=rotate_order):
                quaternion = _quaternion_from_euler_degrees(
                    values,
                    rotate_order=rotate_order,
                )
                euler = _euler_degrees_from_quaternion(
                    quaternion,
                    rotate_order=rotate_order,
                )
                roundtrip = _quaternion_from_euler_degrees(
                    euler,
                    rotate_order=rotate_order,
                )
                dot = abs(sum(a * b for a, b in zip(quaternion, roundtrip)))
                self.assertAlmostEqual(dot, 1.0, places=12)

    def test_append_compound_authored_plugs_enter_edit_and_bake(self):
        """Expand mmdAppend compound inputs while transferring ownership."""
        root = self._import_fixture()
        rig = build_mmd_control_rig(root)
        append_node = (cmds.ls(type="mmdAppend") or [None])[0]
        self.assertTrue(append_node)
        append_joint = (
            cmds.listConnections(
                f"{append_node}.outputRotate",
                source=False,
                destination=True,
                type="joint",
            )
            or []
        )[0]
        append_targets = (
            f"{append_node}.baseRotate",
            f"{append_node}.baseTranslate",
        )
        cmds.setKeyframe(append_node, attribute="baseRotateX", time=0, value=0.0)
        cmds.setKeyframe(append_node, attribute="baseRotateX", time=10, value=15.0)
        cmds.setKeyframe(append_node, attribute="baseTranslateX", time=0, value=0.0)
        cmds.setKeyframe(append_node, attribute="baseTranslateX", time=10, value=0.5)
        original_sources = {}
        for target in append_targets:
            source = (
                cmds.listConnections(
                    f"{target}X",
                    source=True,
                    destination=False,
                    plugs=True,
                )
                or []
            )
            self.assertEqual(len(source), 1)
            original_sources[target] = source[0]

        metadata = read_mmd_control_rig_metadata(root)
        binding = metadata["bindings"]["groove"]
        binding["joint"] = (cmds.ls(append_joint, long=True) or [append_joint])[0]
        binding["jointUuid"] = cmds.ls(append_joint, uuid=True)[0]
        binding["inputKind"] = INPUT_APPEND_BASE
        binding["authoredPlugs"] = list(append_targets)
        binding["authoredPlugRefs"] = [
            {
                "nodeUuid": cmds.ls(append_node, uuid=True)[0],
                "attribute": target.rsplit(".", 1)[-1],
            }
            for target in append_targets
        ]
        cmds.setAttr(
            f"{root}.{ATTR_MMD_CONTROL_RIG_JSON}",
            json.dumps(metadata, ensure_ascii=False),
            type="string",
        )

        edit = enter_mmd_control_rig_edit(root)

        self.assertEqual(edit["state"], "EDIT")
        control = rig.controls["groove"]
        for source_name, control_name in (
            ("baseRotate", "rotate"),
            ("baseTranslate", "translate"),
        ):
            for axis in "XYZ":
                self.assertTrue(
                    cmds.isConnected(
                        f"{control}.{control_name}{axis}",
                        f"{append_node}.{source_name}{axis}",
                    )
                )
        source = (
            cmds.listConnections(
                f"{append_node}.baseRotateX",
                source=True,
                destination=False,
                plugs=True,
            )
            or []
        )
        self.assertEqual(len(source), 1)
        self.assertEqual(source[0].rsplit(".", 1)[-1], "rotateX")
        self.assertEqual(
            cmds.ls(source[0].split(".", 1)[0], uuid=True),
            cmds.ls(control, uuid=True),
        )

        baked = bake_mmd_control_rig(root)

        self.assertEqual(baked["state"], "BAKED")
        for target, control_name in zip(append_targets, ("rotate", "translate")):
            self.assertTrue(
                cmds.isConnected(original_sources[target], f"{target}X")
            )
            self.assertFalse(
                cmds.isConnected(f"{control}.{control_name}X", f"{target}X")
            )

    def test_binding_uuid_authority_survives_joint_solver_and_append_renames(self):
        """Resolve persisted binding nodes by UUID after DAG renames."""
        root = self._import_fixture()
        build_mmd_control_rig(root)
        metadata = read_mmd_control_rig_metadata(root)
        append_node = (cmds.ls(type="mmdAppend") or [None])[0]
        self.assertTrue(append_node)
        append_joint = (
            cmds.listConnections(
                f"{append_node}.outputRotate",
                source=False,
                destination=True,
                type="joint",
            )
            or []
        )[0]
        append_role = "groove"
        append_binding = metadata["bindings"][append_role]
        append_binding.update(
            {
                "joint": (cmds.ls(append_joint, long=True) or [append_joint])[0],
                "jointUuid": cmds.ls(append_joint, uuid=True)[0],
                "inputKind": INPUT_APPEND_BASE,
                "authoredPlugs": [f"{append_node}.baseRotate"],
                "authoredPlugRefs": [
                    {
                        "nodeUuid": cmds.ls(append_node, uuid=True)[0],
                        "attribute": "baseRotate",
                    }
                ],
            }
        )
        self.assertTrue(append_binding["jointUuid"])
        self.assertTrue(append_binding["authoredPlugRefs"])
        append_joint = (
            cmds.ls(append_binding["jointUuid"], long=True) or []
        )[0]
        append_node = (
            cmds.ls(append_binding["authoredPlugRefs"][0]["nodeUuid"], long=True)
            or []
        )[0]
        solver_binding = metadata["bindings"]["left_foot_ik"]
        self.assertTrue(solver_binding["ikSolverUuids"])
        solver = (cmds.ls(solver_binding["ikSolverUuids"][0], long=True) or [])[0]
        cmds.setAttr(
            f"{root}.{ATTR_MMD_CONTROL_RIG_JSON}",
            json.dumps(metadata, ensure_ascii=False),
            type="string",
        )

        renamed_joint = cmds.rename(append_joint, "mmd_control_append_joint_RENAMED")
        renamed_append = cmds.rename(append_node, "mmd_control_append_RENAMED")
        renamed_solver = cmds.rename(solver, "mmd_control_solver_RENAMED")
        renamed_joint = (cmds.ls(renamed_joint, long=True) or [renamed_joint])[0]
        renamed_append = (cmds.ls(renamed_append, long=True) or [renamed_append])[0]
        renamed_solver = (cmds.ls(renamed_solver, long=True) or [renamed_solver])[0]

        edit = enter_mmd_control_rig_edit(root)

        self.assertEqual(edit["state"], "EDIT")
        routes = control_rig_edit_routes_for_joints([renamed_joint])
        self.assertIn(renamed_joint, routes)
        append_targets = {
            row["target"]
            for row in edit["journal"]["channels"]
            if row["target"].startswith(f"{renamed_append}.")
        }
        self.assertTrue(append_targets)
        solver_targets = {
            row["target"]
            for row in edit["journal"]["ikEnabled"]
            if row["target"].startswith(f"{renamed_solver}.")
        }
        self.assertTrue(solver_targets)

        bake_mmd_control_rig(root)
        collected = VmdSceneCollector().collect({"target_model": root})
        self.assertIsInstance(collected["bone_frames"], list)

    def test_bake_failure_restores_edit_graph_and_metadata(self):
        root = self._import_fixture()
        self.assertTrue(
            import_mmd_file(
                _VMD_PATH,
                options={"target_model": root, "pmx_path": _PMX_PATH},
            )
        )
        build_mmd_control_rig(root)
        edit = enter_mmd_control_rig_edit(root)
        graph_before = self._capture_edit_graph(edit["journal"])
        metadata_before = cmds.getAttr(f"{root}.{ATTR_MMD_CONTROL_RIG_JSON}")
        connect_attr = cmds.connectAttr
        failures = [RuntimeError("simulated bake connection failure")]

        def fail_once(*args, **kwargs):
            if failures:
                raise failures.pop()
            return connect_attr(*args, **kwargs)

        with mock.patch.object(cmds, "connectAttr", side_effect=fail_once):
            with self.assertRaisesRegex(RuntimeError, "simulated bake"):
                bake_mmd_control_rig(root)

        self.assertEqual(cmds.getAttr(f"{root}.{ATTR_MMD_CONTROL_RIG_JSON}"), metadata_before)
        self.assertEqual(self._capture_edit_graph(edit["journal"]), graph_before)
        self.assertEqual(read_mmd_control_rig_metadata(root)["state"], "EDIT")

    def test_restore_resolves_renamed_animation_source_by_uuid(self):
        root = self._import_fixture()
        self.assertTrue(
            import_mmd_file(
                _VMD_PATH,
                options={"target_model": root, "pmx_path": _PMX_PATH},
            )
        )
        build_mmd_control_rig(root)
        edit = enter_mmd_control_rig_edit(root)
        row = next(row for row in edit["journal"]["channels"] if row["source"])
        source_attribute = row["sourceRef"]["attribute"]
        source_uuid = row["sourceRef"]["nodeUuid"]
        renamed = cmds.rename(row["source"].split(".", 1)[0], "renamedControlRigAnim")
        renamed = (cmds.ls(renamed, long=True) or [renamed])[0]

        restored = restore_mmd_control_rig_attached(root)

        resolved_source = f"{(cmds.ls(source_uuid, long=True) or [renamed])[0]}.{source_attribute}"
        self.assertEqual(restored["state"], "ATTACHED")
        if row.get("layerRoute"):
            self.assertTrue(
                cmds.isConnected(resolved_source, row["layerRoute"]["blend"])
            )
        else:
            self.assertTrue(cmds.isConnected(resolved_source, row["target"]))

    def test_restore_failure_restores_edit_graph_and_metadata(self):
        root = self._import_fixture()
        self.assertTrue(
            import_mmd_file(
                _VMD_PATH,
                options={"target_model": root, "pmx_path": _PMX_PATH},
            )
        )
        build_mmd_control_rig(root)
        edit = enter_mmd_control_rig_edit(root)
        graph_before = self._capture_edit_graph(edit["journal"])
        metadata_before = cmds.getAttr(f"{root}.{ATTR_MMD_CONTROL_RIG_JSON}")
        connect_attr = cmds.connectAttr
        failures = [RuntimeError("simulated restore connection failure")]

        def fail_once(*args, **kwargs):
            if failures:
                raise failures.pop()
            return connect_attr(*args, **kwargs)

        with mock.patch.object(cmds, "connectAttr", side_effect=fail_once):
            with self.assertRaisesRegex(RuntimeError, "simulated restore"):
                restore_mmd_control_rig_attached(root)

        self.assertEqual(cmds.getAttr(f"{root}.{ATTR_MMD_CONTROL_RIG_JSON}"), metadata_before)
        self.assertEqual(self._capture_edit_graph(edit["journal"]), graph_before)
        self.assertEqual(read_mmd_control_rig_metadata(root)["state"], "EDIT")

    def test_restore_missing_animation_source_fails_before_graph_mutation(self):
        root = self._import_fixture()
        self.assertTrue(
            import_mmd_file(
                _VMD_PATH,
                options={"target_model": root, "pmx_path": _PMX_PATH},
            )
        )
        build_mmd_control_rig(root)
        edit = enter_mmd_control_rig_edit(root)
        row = next(row for row in edit["journal"]["channels"] if row["source"])
        metadata_before = cmds.getAttr(f"{root}.{ATTR_MMD_CONTROL_RIG_JSON}")
        cmds.delete(row["source"].split(".", 1)[0])
        route_target = (
            row["layerRoute"]["blend"] if row.get("layerRoute") else row["target"]
        )
        control_to_target = cmds.isConnected(row["control"], route_target)

        with self.assertRaisesRegex(MmdControlRigBuildError, "journal source node is missing"):
            restore_mmd_control_rig_attached(root)

        self.assertTrue(control_to_target)
        self.assertTrue(cmds.isConnected(row["control"], route_target))
        self.assertEqual(cmds.getAttr(f"{root}.{ATTR_MMD_CONTROL_RIG_JSON}"), metadata_before)
        self.assertEqual(read_mmd_control_rig_metadata(root)["state"], "EDIT")

    def test_baked_collector_exports_append_inputs_and_ik_states(self):
        root = self._import_fixture()
        self.assertTrue(
            import_mmd_file(
                _VMD_PATH,
                options={"target_model": root, "pmx_path": _PMX_PATH},
            )
        )
        build_mmd_control_rig(root)
        enter_mmd_control_rig_edit(root)
        bake_mmd_control_rig(root)
        append_node = (cmds.ls(type="mmdAppend") or [None])[0]
        self.assertTrue(append_node)
        append_joint = (
            cmds.listConnections(
                f"{append_node}.outputRotate",
                source=False,
                destination=True,
                type="joint",
            )
            or []
        )[0]
        append_bone = cmds.getAttr(f"{append_joint}.mmd_bone_name")
        cmds.setKeyframe(append_node, attribute="baseRotateX", time=0, value=0.0)
        cmds.setKeyframe(append_node, attribute="baseRotateX", time=10, value=15.0)
        collected = VmdSceneCollector().collect({"target_model": root})
        output_path = self.get_temp_filename("mmd_control_rig_baked.vmd")
        VmdExporter().export_vmd_animation(output_path, collected)
        parsed = VmdData().parse_file(output_path)

        exported_bones = {frame.bone_name for frame in parsed.bone_frames}
        self.assertIn(append_bone, exported_bones)
        self.assertTrue(parsed.ik_show_hide_frames)
        exported_ik = {
            name
            for frame in parsed.ik_show_hide_frames
            for name, _enabled in frame.ik_states
        }
        self.assertTrue({"左足ＩＫ", "右足ＩＫ"}.intersection(exported_ik) or {"左足IK", "右足IK"}.intersection(exported_ik))

    def test_control_rig_vmd_roundtrip_preserves_world_matrices_and_ik(self):
        root = self._import_fixture()
        self.assertTrue(
            import_mmd_file(
                _VMD_PATH,
                options={"target_model": root, "pmx_path": _PMX_PATH},
            )
        )
        rig = build_mmd_control_rig(root)
        enter_mmd_control_rig_edit(root)
        frame = 3
        center = rig.controls["center"]
        edited_tx = float(cmds.getAttr(f"{center}.translateX", time=frame)) + 1.25
        keyed = cmds.setKeyframe(
            center, attribute="translateX", time=frame, value=edited_tx
        )
        self.assertTrue(keyed)
        self.assertIn(
            frame,
            cmds.keyframe(
                center, attribute="translateX", query=True, timeChange=True
            )
            or [],
        )
        left_ik = rig.controls["left_foot_ik"]
        cmds.setKeyframe(left_ik, attribute="ikEnabled", time=frame, value=0)
        bake_mmd_control_rig(root)

        frames = (0, 1, 3, 5)
        source_world = self._capture_indexed_world_matrices(root, frames)
        source_ik = self._capture_ik_states(root, frames)
        collected = VmdSceneCollector().collect({"target_model": root})
        collected_bone_times = {
            item["frame_number"] for item in collected["bone_frames"]
        }
        self.assertIn(frame, collected_bone_times)
        output_path = self.get_temp_filename("mmd_control_rig_roundtrip.vmd")
        VmdExporter().export_vmd_animation(output_path, collected)
        parsed = VmdData().parse_file(output_path)
        self.assertTrue(any(item.frame_number == frame for item in parsed.bone_frames))
        self.assertTrue(any(item.frame_number == frame for item in parsed.ik_show_hide_frames))

        cmds.file(new=True, force=True)
        fresh_root = self._import_fixture()
        self.assertTrue(
            import_mmd_file(
                output_path,
                options={"target_model": fresh_root, "pmx_path": _PMX_PATH},
            )
        )
        fresh_world = self._capture_indexed_world_matrices(fresh_root, frames)
        fresh_ik = self._capture_ik_states(fresh_root, frames)

        self.assertEqual(set(source_world), set(fresh_world))
        self.assertEqual(source_ik, fresh_ik)
        matrix_errors = [
            (abs(actual - expected), key, index, actual, expected)
            for key in source_world
            for index, (actual, expected) in enumerate(
                zip(source_world[key], fresh_world[key])
            )
        ]
        self.assertLess(
            max(matrix_errors)[0],
            5e-3,
            {
                "largest": sorted(matrix_errors, reverse=True)[:10],
                "earliest": sorted(
                    (item for item in matrix_errors if item[0] > 1e-4),
                    key=lambda item: (item[1], item[2]),
                )[:20],
            },
        )

    def _capture_edit_graph(self, journal):
        def _stable_value(value):
            # Maya may re-evaluate an unchanged animLayer/quaternion path by a
            # few floating-point ulps while a failed transaction is rolled
            # back.  Connection topology remains exact; normalize only scalar
            # payload noise so the assertion does not confuse evaluation
            # precision with a graph mutation.
            if isinstance(value, (list, tuple)):
                return tuple(_stable_value(item) for item in value)
            if isinstance(value, float):
                # Maya may re-evaluate quaternion/animCurve plugs by a few
                # 1e-8 ulps while a transaction rolls back.  Keep graph
                # comparisons deterministic without masking real changes.
                if abs(value) < 1e-5:
                    return 0.0
                return round(value, 6)
            return value

        plugs = {
            row[key]
            for section in ("channels", "ikEnabled")
            for row in journal.get(section, [])
            for key in ("control", "target")
        }
        plugs.update(row["control"] for row in journal.get("offsetParentMatrix", []))
        return {
            plug: (
                tuple(
                    cmds.listConnections(
                        plug, source=True, destination=False, plugs=True
                    )
                    or []
                ),
                _stable_value(cmds.getAttr(plug)),
            )
            for plug in sorted(plugs)
        }

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

    def _capture_ik_states(self, root, frames):
        nodes = collect_ik_nodes_by_bone_name(target_model=root)
        restore = cmds.currentTime(query=True)
        try:
            return {
                (name, frame): bool(cmds.getAttr(f"{node}.enabled", time=frame))
                for name, node in nodes.items()
                for frame in frames
            }
        finally:
            cmds.currentTime(restore, edit=True)

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

    def test_builder_uses_absolute_model_namespace_when_it_is_current(self):
        root = self._import_fixture(use_namespace=True)
        root = (cmds.ls(root, long=True) or [root])[0]
        root_leaf = root.rsplit("|", 1)[-1]
        namespace = root_leaf.rsplit(":", 1)[0]

        cmds.namespace(set=f":{namespace}")
        try:
            rig = build_mmd_control_rig(root)
        finally:
            cmds.namespace(set=":")

        control_group_leaf = rig.control_group.rsplit("|", 1)[-1]
        self.assertEqual(control_group_leaf, f"{namespace}:Controls")
        self.assertNotIn(f"{namespace}:{namespace}:", control_group_leaf)


if __name__ == "__main__":
    import unittest

    unittest.main()
