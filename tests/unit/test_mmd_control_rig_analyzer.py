"""Pure-Python tests for MMD-native control-rig input classification."""

import unittest

from mmd_tools.core.mmd_control_rig_analyzer import (
    INPUT_APPEND_BASE,
    INPUT_DIRECT_CHANNEL,
    INPUT_IK_CONTROLLER,
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
    _upgrade_binding_authority,
    _parent_zero_groups,
    _should_build_role_control,
    resolve_mmd_control_rig_binding_authored_plugs,
    resolve_mmd_control_rig_binding_ik_solvers,
    resolve_mmd_control_rig_binding_joint,
)
from mmd_tools.core.pmx_data.bone import PmxBoneFlag


def _bone(index, name, *, pmx_flags=0, incoming=(), ik_solvers=()):
    return MmdControlRigBoneFact(
        joint=f"|model|bone_{index}",
        mmd_name=name,
        bone_index=index,
        pmx_flags=pmx_flags,
        incoming=tuple(incoming),
        ik_solvers=tuple(ik_solvers),
    )


def _connection(source, destination, node_type):
    return MmdControlRigConnectionFact(source, destination, node_type)


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
