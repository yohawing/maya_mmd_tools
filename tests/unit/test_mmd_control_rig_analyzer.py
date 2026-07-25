"""Pure-Python tests for MMD-native control-rig input classification."""

import math
from types import SimpleNamespace
import unittest

from mmd_tools.core.mmd_control_rig_analyzer import (
    INPUT_APPEND_BASE,
    INPUT_DIRECT_CHANNEL,
    INPUT_IK_CONTROLLER,
    INPUT_IK_LINK_INPUT,
    INPUT_SOLVER_OUTPUT,
    INPUT_UNSUPPORTED,
    STATUS_BLOCKED,
    STATUS_FALLBACK,
    STATUS_READY,
    MmdControlRigBoneFact,
    MmdControlRigConnectionFact,
    classify_mmd_control_rig,
)
from mmd_tools.core.mmd_control_rig_builder import (
    _apply_fallback_role_aliases,
    _control_curve_templates,
    _control_curve_template_role,
    _control_shape_rotation,
    _rotate_shape_point,
    _shortest_arc_from_positive_z,
    _upgrade_binding_authority,
    _parent_zero_groups,
    _should_build_role_control,
    resolve_mmd_control_rig_binding_authored_plugs,
    resolve_mmd_control_rig_binding_ik_solvers,
    resolve_mmd_control_rig_binding_joint,
)
from mmd_tools.core.pmx_data.bone import PmxBoneFlag


def _bone(index, name, *, pmx_flags=0, incoming=(), ik_solvers=(), solver_input_plugs=()):
    return MmdControlRigBoneFact(
        joint=f"|model|bone_{index}",
        mmd_name=name,
        bone_index=index,
        pmx_flags=pmx_flags,
        incoming=tuple(incoming),
        ik_solvers=tuple(ik_solvers),
        solver_input_plugs=tuple(solver_input_plugs),
    )


def _connection(source, destination, node_type):
    return MmdControlRigConnectionFact(source, destination, node_type)


class _ShapeOrientationFake:
    """Minimal PMX metadata reader for display-only orientation tests."""

    def __init__(self, values):
        self.values = values

    def attributeQuery(self, attribute, *, node, exists):
        return exists and f"{node}.{attribute}" in self.values

    def getAttr(self, plug):
        return self.values[plug]

    def listRelatives(self, node, **kwargs):
        return []


class _HierarchyFake:
    """Minimal parent graph that rejects introducing a descendant cycle."""

    def __init__(self):
        self.parent_by_child = {
            "master_CTRL": "master_ZERO",
            "center_CTRL": "center_ZERO",
        }

    def parent(self, child, parent):
        ancestor = parent
        while ancestor in self.parent_by_child:
            if ancestor == child:
                raise AssertionError(f"self-parent cycle: {child} -> {parent}")
            ancestor = self.parent_by_child[ancestor]
        self.parent_by_child[child] = parent


class MmdControlRigCurveTemplateTest(unittest.TestCase):
    """Validate the bundled artist-authored controller shape snapshot."""

    def test_curve_library_has_mvp_roles_and_shared_finger_shape(self):
        templates = _control_curve_templates()

        self.assertEqual(len(templates["center"]), 4)
        self.assertEqual(len(templates["upper_body"]), 2)
        self.assertIn("finger", templates)
        self.assertEqual(len(templates["left_leg"]), 1)
        self.assertEqual(len(templates["left_knee"]), 1)
        self.assertEqual(len(templates["right_leg"]), 1)
        self.assertEqual(len(templates["right_knee"]), 1)
        self.assertNotIn("groove", templates)
        self.assertEqual(_control_curve_template_role("left_middle_1"), "finger")
        self.assertEqual(_control_curve_template_role("right_thumb_2"), "finger")
        for role in (
            "waist",
            "left_foot_ik_parent",
            "right_foot_ik_parent",
            "left_toe_ik",
            "right_toe_ik",
        ):
            with self.subTest(role=role):
                self.assertEqual(_control_curve_template_role(role), "circle")
        self.assertEqual(templates["circle"], templates["left_arm"])
        self.assertEqual(
            templates["finger"][0]["points"][0],
            [0.109158265, 0.055801374, 0.031276997],
        )
        self.assertEqual(len(templates["finger"][0]["points"]), 21)
        self.assertTrue(all(shape["points"] for shapes in templates.values() for shape in shapes))
        self.assertTrue(all(shape["knots"] for shapes in templates.values() for shape in shapes))

    def test_shape_only_shortest_arc_aligns_positive_z_without_scaling(self):
        direction = (2.0, 3.0, -4.0)
        rotation = _shortest_arc_from_positive_z(direction)

        aligned = _rotate_shape_point((0.0, 0.0, 1.0), rotation)
        direction_length = math.sqrt(sum(value * value for value in direction))
        expected = tuple(value / direction_length for value in direction)

        for actual, target in zip(aligned, expected):
            self.assertAlmostEqual(actual, target, places=12)
        arbitrary = (0.25, -0.5, 2.0)
        rotated = _rotate_shape_point(arbitrary, rotation)
        self.assertAlmostEqual(
            sum(value * value for value in rotated),
            sum(value * value for value in arbitrary),
            places=12,
        )

    def test_shape_only_shortest_arc_handles_aligned_opposite_and_zero(self):
        aligned = _shortest_arc_from_positive_z((0.0, 0.0, 5.0))
        opposite = _shortest_arc_from_positive_z((0.0, 0.0, -2.0))

        self.assertEqual(_rotate_shape_point((1.0, 2.0, 3.0), aligned), (1.0, 2.0, 3.0))
        self.assertEqual(_rotate_shape_point((0.0, 0.0, 1.0), opposite), (0.0, 0.0, -1.0))
        self.assertIsNone(_shortest_arc_from_positive_z((0.0, 0.0, 0.0)))

    def test_fk_without_local_axis_uses_pmx_tail_but_world_controls_do_not(self):
        values = {
            "arm.mmd_bone_flags": 0,
            "arm.mmd_connect_index": 11,
            "arm.mmd_pmx_rest_position": [(0.0, 0.0, 0.0)],
            "elbow.mmd_pmx_rest_position": [(2.0, 0.0, 0.0)],
        }
        cmds = _ShapeOrientationFake(values)
        binding = SimpleNamespace(joint="arm", bone_index=10, pmx_flags=0)

        rotation = _control_shape_rotation(
            cmds,
            "root",
            "left_arm",
            binding,
            {10: "arm", 11: "elbow"},
        )

        aligned = _rotate_shape_point((0.0, 0.0, 1.0), rotation)
        self.assertAlmostEqual(aligned[0], 1.0)
        self.assertAlmostEqual(aligned[1], 0.0)
        self.assertAlmostEqual(aligned[2], 0.0)
        self.assertIsNone(
            _control_shape_rotation(
                cmds,
                "root",
                "center",
                binding,
                {10: "arm", 11: "elbow"},
            )
        )

        local_axis_binding = SimpleNamespace(
            joint="arm",
            bone_index=10,
            pmx_flags=int(PmxBoneFlag.LOCAL_AXIS),
        )
        self.assertIsNone(
            _control_shape_rotation(
                cmds,
                "root",
                "left_arm",
                local_axis_binding,
                {10: "arm", 11: "elbow"},
            )
        )

    def test_arm_chain_uses_sized_mmd_z_primary_circles(self):
        templates = _control_curve_templates()
        expected_first_points = {
            "arm": [0.7836116248912245, 0.7836116248912245, 0],
            "elbow": [0.61712993, 0.61712993, 0.0],
            "wrist": [0.541400314, 0.541400314, 0.0],
        }

        for part, first_point in expected_first_points.items():
            left = templates[f"left_{part}"][0]
            right = templates[f"right_{part}"][0]
            with self.subTest(part=part):
                self.assertEqual(left, right)
                self.assertEqual(left["degree"], 3)
                self.assertTrue(left["periodic"])
                self.assertEqual(len(left["points"]), 11)
                self.assertEqual(left["points"][0], first_point)
                self.assertTrue(all(abs(point[2]) < 1.0e-12 for point in left["points"]))

    def test_leg_curve_template_uses_edited_thigh_basis(self):
        templates = _control_curve_templates()
        left_points = templates["left_leg"][0]["points"]
        right_points = templates["right_leg"][0]["points"]

        self.assertEqual(left_points[0], [-0.466608285, 2.472883346, 0.891672144])
        self.assertEqual(len(left_points), 23)
        self.assertEqual(
            right_points,
            [[-point[0], point[1], point[2]] for point in left_points],
        )

    def test_knee_curve_template_uses_edited_basis(self):
        templates = _control_curve_templates()
        left_points = templates["left_knee"][0]["points"]
        right_points = templates["right_knee"][0]["points"]

        self.assertEqual(left_points[0], [-0.819720666, 0.767543818, 0.019164612])
        self.assertEqual(len(left_points), 23)
        self.assertEqual(
            right_points,
            [[-point[0], point[1], point[2]] for point in left_points],
        )

    def test_lower_body_curve_template_uses_edited_basis(self):
        shape = _control_curve_templates()["lower_body"][0]

        self.assertEqual(shape["degree"], 3)
        self.assertTrue(shape["periodic"])
        self.assertEqual(len(shape["points"]), 11)
        self.assertEqual(shape["points"][0], [-2.223043634, 2.000169001, 0.199426674])

    def test_upper_body_templates_use_edited_x_rotated_basis(self):
        templates = _control_curve_templates()

        self.assertEqual(templates["upper_body"][0]["points"][0], [1.617431786, 1.618510388, 0.067250332])
        self.assertEqual(templates["upper_body"][1]["points"][0], [1.617431786, 1.618510388, 0.109333963])
        self.assertEqual(templates["upper_body2"][0]["points"][0], [1.312549415, 1.429059275, -0.067284223])
        self.assertEqual(templates["upper_body2"][1]["points"][0], [1.312549415, 1.429059275, -0.033129316])

    def test_neck_and_head_templates_use_edited_x_rotated_basis(self):
        templates = _control_curve_templates()
        neck = templates["neck"]
        head = templates["head"]

        self.assertEqual(neck[0]["points"][0], [0.628942094, 0.650968315, 0.43873122])
        self.assertEqual(neck[1]["points"][0], [0.628942094, 0.650968315, 0.472886127])
        self.assertTrue(all(point[2] == neck[0]["points"][0][2] for point in neck[0]["points"]))
        self.assertEqual(head[0]["points"][0], [1.694039627, 1.782582108, 1.960093697])
        self.assertEqual(head[1]["points"][0], [1.693861783, 1.782391893, 2.001040239])

    def test_shoulder_template_uses_edited_basis_and_exact_mirror(self):
        templates = _control_curve_templates()
        left_points = templates["left_shoulder"][0]["points"]
        right_points = templates["right_shoulder"][0]["points"]

        self.assertEqual(left_points[0], [0.647078708, 0.274496301, 0.284784447])
        self.assertEqual(len(left_points), 21)
        self.assertEqual(
            right_points,
            [[-point[0], point[1], point[2]] for point in left_points],
        )


class _UuidBindingFake:
    """Resolve renamed scene nodes from stable UUIDs in binding metadata."""

    def __init__(self):
        self.uuid_to_node = {
            "joint-uuid": "|model_NS|center_RENAMED",
            "solver-uuid": "|model_NS|ik_RENAMED",
            "append-uuid": "|model_NS|append_RENAMED",
        }
        self.node_to_uuid = {
            node: uuid for uuid, node in self.uuid_to_node.items()
        }

    def ls(self, value, long=False, uuid=False):  # noqa: A002
        if uuid:
            return [self.node_to_uuid[value]] if value in self.node_to_uuid else []
        if value in self.uuid_to_node:
            return [self.uuid_to_node[value]]
        if value in self.node_to_uuid:
            return [value]
        return []


class TestMmdControlRigAnalyzer(unittest.TestCase):
    def test_legacy_binding_metadata_upgrades_to_uuid_authority(self):
        cmds = _UuidBindingFake()
        metadata = {
            "version": 1,
            "bindings": {
                "center": {
                    "joint": "|model_NS|center_RENAMED",
                    "ikSolvers": ["|model_NS|ik_RENAMED"],
                    "authoredPlugs": ["|model_NS|append_RENAMED.baseRotate"],
                }
            },
        }

        _upgrade_binding_authority(cmds, metadata)

        binding = metadata["bindings"]["center"]
        self.assertEqual(metadata["version"], 2)
        self.assertEqual(binding["jointUuid"], "joint-uuid")
        self.assertEqual(binding["ikSolverUuids"], ["solver-uuid"])
        self.assertEqual(
            binding["authoredPlugRefs"],
            [{"nodeUuid": "append-uuid", "attribute": "baseRotate"}],
        )

    def test_binding_uuid_fields_resolve_renamed_nodes_and_legacy_names_remain_supported(self):
        cmds = _UuidBindingFake()
        binding = {
            "joint": "|model_NS|center_OLD",
            "jointUuid": "joint-uuid",
            "ikSolvers": ["|model_NS|ik_OLD"],
            "ikSolverUuids": ["solver-uuid"],
            "authoredPlugs": ["|model_NS|append_OLD.baseRotate"],
            "authoredPlugRefs": [
                {"nodeUuid": "append-uuid", "attribute": "baseRotate"}
            ],
        }

        self.assertEqual(
            resolve_mmd_control_rig_binding_joint(cmds, binding),
            "|model_NS|center_RENAMED",
        )
        self.assertEqual(
            resolve_mmd_control_rig_binding_ik_solvers(cmds, binding),
            ("|model_NS|ik_RENAMED",),
        )
        self.assertEqual(
            resolve_mmd_control_rig_binding_authored_plugs(cmds, binding),
            ("|model_NS|append_RENAMED.baseRotate",),
        )

        legacy = {
            "joint": "|model_NS|center_RENAMED",
            "ikSolvers": ["|model_NS|ik_RENAMED"],
            "authoredPlugs": ["|model_NS|append_RENAMED.baseRotate"],
        }
        self.assertEqual(
            resolve_mmd_control_rig_binding_joint(cmds, legacy),
            "|model_NS|center_RENAMED",
        )
        self.assertEqual(
            resolve_mmd_control_rig_binding_ik_solvers(cmds, legacy),
            ("|model_NS|ik_RENAMED",),
        )
        self.assertEqual(
            resolve_mmd_control_rig_binding_authored_plugs(cmds, legacy),
            ("|model_NS|append_RENAMED.baseRotate",),
        )

    def test_resolves_mvp_roles_and_semistandard_fallbacks(self):
        facts = [
            _bone(0, "センター"),
            _bone(1, "左足ＩＫ", ik_solvers=("left_leg_ik_mmdCcdIk",)),
            _bone(2, "右足IK", ik_solvers=("right_leg_ik_mmdCcdIk",)),
        ]

        spec = classify_mmd_control_rig("|model", facts)
        roles = spec.roles_by_name

        self.assertEqual(roles["master"].status, STATUS_FALLBACK)
        self.assertEqual(roles["master"].fallback, "model_root")
        self.assertEqual(roles["groove"].status, STATUS_FALLBACK)
        self.assertEqual(roles["groove"].fallback, "center")
        self.assertEqual(roles["left_foot_ik"].status, STATUS_READY)
        self.assertEqual(
            roles["left_foot_ik"].binding.input_kind,
            INPUT_IK_CONTROLLER,
        )
        self.assertTrue(spec.can_build_mvp)

    def test_role_control_builder_aliases_semantic_fallback_but_keeps_model_root(self):
        facts = [_bone(0, "センター")]

        spec = classify_mmd_control_rig("|model", facts)
        roles = spec.roles_by_name
        controls = {"master": "master_CTRL", "center": "center_CTRL"}
        zero_groups = {"master": "master_ZERO", "center": "center_ZERO"}
        bindings = {
            "master": {"joint": "|model"},
            "center": {"joint": "|model|bone_0"},
        }

        _apply_fallback_role_aliases(
            spec.roles,
            controls,
            zero_groups,
            bindings,
        )

        self.assertFalse(_should_build_role_control(roles["groove"]))
        self.assertTrue(_should_build_role_control(roles["master"]))
        self.assertEqual(controls["groove"], controls["center"])
        self.assertEqual(zero_groups["groove"], zero_groups["center"])
        self.assertEqual(bindings["groove"]["joint"], bindings["center"]["joint"])
        self.assertEqual(len(set(controls.values())), 2)

    def test_fallback_aliases_are_added_after_concrete_hierarchy_parenting(self):
        spec = classify_mmd_control_rig("|model", [_bone(0, "センター")])
        concrete_controls = {"master": "master_CTRL", "center": "center_CTRL"}
        concrete_zeros = {"master": "master_ZERO", "center": "center_ZERO"}
        bindings = {
            "master": {"joint": "|model"},
            "center": {"joint": "|model|bone_0"},
        }

        fake = _HierarchyFake()
        _parent_zero_groups(fake, concrete_zeros, concrete_controls)
        _apply_fallback_role_aliases(
            spec.roles,
            concrete_controls,
            concrete_zeros,
            bindings,
        )

        self.assertEqual(concrete_zeros["groove"], "center_ZERO")
        self.assertEqual(concrete_controls["groove"], "center_CTRL")

        aliased_fake = _HierarchyFake()
        aliased_controls = {"master": "master_CTRL", "center": "center_CTRL"}
        aliased_zeros = {"master": "master_ZERO", "center": "center_ZERO"}
        aliased_bindings = {
            "master": {"joint": "|model"},
            "center": {"joint": "|model|bone_0"},
        }
        _apply_fallback_role_aliases(
            spec.roles,
            aliased_controls,
            aliased_zeros,
            aliased_bindings,
        )
        with self.assertRaises(AssertionError):
            _parent_zero_groups(aliased_fake, aliased_zeros, aliased_controls)

    def test_finger_roles_resolve_variants_and_parent_as_fk_chains(self):
        facts = [
            _bone(10, "左親指０"),
            _bone(11, "左人差指１"),
            _bone(12, "右人指３"),
        ]

        spec = classify_mmd_control_rig("|model", facts)
        roles = spec.roles_by_name

        self.assertEqual(roles["left_thumb_0"].status, STATUS_READY)
        self.assertEqual(roles["left_index_1"].status, STATUS_READY)
        self.assertEqual(roles["right_index_3"].status, STATUS_READY)
        self.assertEqual(
            len(
                [
                    role
                    for role in roles
                    if role.startswith(
                        (
                            "left_thumb_",
                            "left_index_",
                            "left_middle_",
                            "left_ring_",
                            "left_pinky_",
                            "right_thumb_",
                            "right_index_",
                            "right_middle_",
                            "right_ring_",
                            "right_pinky_",
                        )
                    )
                ]
            ),
            30,
        )

        fake = _HierarchyFake()
        controls = {
            "left_wrist": "left_wrist_CTRL",
            "left_middle_1": "left_middle_1_CTRL",
            "left_middle_2": "left_middle_2_CTRL",
            "left_middle_3": "left_middle_3_CTRL",
        }
        zero_groups = {
            role: control.replace("_CTRL", "_ZERO")
            for role, control in controls.items()
        }
        _parent_zero_groups(fake, zero_groups, controls)

        self.assertEqual(fake.parent_by_child["left_middle_1_ZERO"], "left_wrist_CTRL")
        self.assertEqual(fake.parent_by_child["left_middle_2_ZERO"], "left_middle_1_CTRL")
        self.assertEqual(fake.parent_by_child["left_middle_3_ZERO"], "left_middle_2_CTRL")

    def test_p0_optional_roles_resolve_and_parent_through_available_chains(self):
        facts = [
            _bone(20, "腰"),
            _bone(21, "左足IK親"),
            _bone(22, "左つま先ＩＫ", ik_solvers=("left_toe_mmdCcdIk",)),
        ]

        roles = classify_mmd_control_rig("|model", facts).roles_by_name

        self.assertEqual(roles["waist"].status, STATUS_READY)
        self.assertEqual(roles["left_foot_ik_parent"].status, STATUS_READY)
        self.assertEqual(roles["left_toe_ik"].status, STATUS_READY)
        self.assertEqual(roles["left_toe_ik"].binding.input_kind, INPUT_IK_CONTROLLER)

        fake = _HierarchyFake()
        controls = {
            "master": "master_CTRL",
            "groove": "groove_CTRL",
            "waist": "waist_CTRL",
            "upper_body": "upper_body_CTRL",
            "lower_body": "lower_body_CTRL",
            "left_foot_ik_parent": "left_foot_ik_parent_CTRL",
            "left_foot_ik": "left_foot_ik_CTRL",
            "left_toe_ik": "left_toe_ik_CTRL",
        }
        zero_groups = {
            role: control.replace("_CTRL", "_ZERO")
            for role, control in controls.items()
        }
        _parent_zero_groups(fake, zero_groups, controls)

        self.assertEqual(fake.parent_by_child["waist_ZERO"], "groove_CTRL")
        self.assertEqual(fake.parent_by_child["upper_body_ZERO"], "waist_CTRL")
        self.assertEqual(fake.parent_by_child["lower_body_ZERO"], "waist_CTRL")
        self.assertEqual(fake.parent_by_child["left_foot_ik_parent_ZERO"], "master_CTRL")
        self.assertEqual(fake.parent_by_child["left_foot_ik_ZERO"], "left_foot_ik_parent_CTRL")
        self.assertEqual(fake.parent_by_child["left_toe_ik_ZERO"], "left_foot_ik_CTRL")

    def test_append_output_routes_controller_to_base_plugs(self):
        upper = _bone(
            3,
            "上半身",
            incoming=(
                _connection(
                    "upper_mmdAppend.outputRotate",
                    "|model|bone_3.rotate",
                    "mmdAppend",
                ),
                _connection(
                    "upper_mmdAppend.outputTranslate",
                    "|model|bone_3.translate",
                    "mmdAppend",
                ),
            ),
        )

        spec = classify_mmd_control_rig("|model", [upper])
        binding = spec.bones[0]

        self.assertEqual(binding.input_kind, INPUT_APPEND_BASE)
        self.assertEqual(
            binding.authored_plugs,
            ("upper_mmdAppend.baseRotate", "upper_mmdAppend.baseTranslate"),
        )
        self.assertFalse(binding.blocked)

    def test_solver_outputs_physics_and_external_writers_fail_closed(self):
        solver_link = _bone(
            0,
            "左ひざ",
            incoming=(
                _connection(
                    "left_leg_ik_mmdCcdIk.outputRotate[0]",
                    "|model|bone_0.rotate",
                    "mmdCcdIk",
                ),
            ),
        )
        physics = _bone(
            1,
            "髪1",
            incoming=(
                _connection(
                    "hair_driver.outputRotate",
                    "|model|bone_1.rotate",
                    "mmdPhysicsBoneDriver",
                ),
            ),
        )
        external = _bone(
            2,
            "補助",
            incoming=(
                _connection(
                    "external_constraint.constraintRotate",
                    "|model|bone_2.rotate",
                    "parentConstraint",
                ),
            ),
        )

        spec = classify_mmd_control_rig("|model", [solver_link, physics, external])
        by_name = {binding.mmd_name: binding for binding in spec.bones}

        self.assertEqual(by_name["左ひざ"].input_kind, INPUT_SOLVER_OUTPUT)
        self.assertEqual(by_name["髪1"].input_kind, INPUT_UNSUPPORTED)
        self.assertEqual(by_name["補助"].input_kind, INPUT_UNSUPPORTED)
        self.assertTrue(all(binding.blocked for binding in by_name.values()))

    def test_thigh_role_uses_pre_solver_input_without_claiming_solver_output(self):
        thigh = _bone(
            0,
            "左足",
            incoming=(
                _connection(
                    "left_leg_ik_mmdCcdIk.outputRotate[1]",
                    "|model|bone_0.rotate",
                    "mmdCcdIk",
                ),
            ),
            solver_input_plugs=tuple(
                f"left_leg_ik_mmdCcdIk.inputRotate[7].inputRotateElement{axis}"
                for axis in "XYZ"
            ),
        )

        spec = classify_mmd_control_rig("|model", [thigh])
        role = spec.roles_by_name["left_leg"]
        bone = next(binding for binding in spec.bones if binding.mmd_name == "左足")

        self.assertEqual(bone.input_kind, INPUT_SOLVER_OUTPUT)
        self.assertTrue(bone.blocked)
        self.assertEqual(role.status, STATUS_READY)
        self.assertEqual(role.binding.input_kind, INPUT_IK_LINK_INPUT)
        self.assertEqual(role.binding.authored_plugs, thigh.solver_input_plugs)

    def test_animation_stack_remains_a_direct_authored_channel(self):
        center = _bone(
            0,
            "センター",
            incoming=(
                _connection(
                    "center_translate.output",
                    "|model|bone_0.translateX",
                    "animCurveTL",
                ),
                _connection(
                    "center_layer.output",
                    "|model|bone_0.rotateX",
                    "animBlendNodeAdditiveRotation",
                ),
            ),
        )

        spec = classify_mmd_control_rig("|model", [center])

        self.assertEqual(spec.bones[0].input_kind, INPUT_DIRECT_CHANNEL)
        self.assertFalse(spec.bones[0].blocked)

    def test_after_physics_metadata_blocks_control_ownership(self):
        physics_helper = _bone(
            0,
            "物理補助",
            pmx_flags=int(PmxBoneFlag.DEFORM_AFTER_PHYSICS),
        )

        spec = classify_mmd_control_rig("|model", [physics_helper])

        self.assertEqual(spec.bones[0].input_kind, INPUT_UNSUPPORTED)
        self.assertIn("after-physics bone", spec.bones[0].blockers[0])

    def test_display_frame_metadata_is_preserved_in_spec(self):
        frames = (
            {
                "name": "ＩＫ",
                "name_english": "IK",
                "special_flag": 0,
                "elements": [{"type": 0, "index": 1}],
            },
        )

        spec = classify_mmd_control_rig(
            "|model",
            [_bone(1, "センター")],
            display_frames=frames,
        )

        self.assertEqual(spec.to_dict()["displayFrames"], list(frames))

    def test_missing_solver_and_duplicate_role_are_reported_deterministically(self):
        facts = [
            _bone(8, "左足IK"),
            _bone(2, "左足ＩＫ"),
            _bone(3, "右足IK", ik_solvers=("right_leg_ik_mmdCcdIk",)),
            _bone(1, "センター"),
        ]

        spec = classify_mmd_control_rig("|model", facts)
        left = spec.roles_by_name["left_foot_ik"]

        self.assertEqual(left.binding.bone_index, 2)
        self.assertEqual(left.status, STATUS_BLOCKED)
        self.assertIn("duplicate MMD bone candidates", left.warnings[0])
        self.assertIn("no owned mmdCcdIk solver", left.blockers[0])
        self.assertFalse(spec.can_build_mvp)
        self.assertEqual(spec.to_dict()["schema"], "mmd_tools.mmd_control_rig_spec")


if __name__ == "__main__":
    unittest.main()
