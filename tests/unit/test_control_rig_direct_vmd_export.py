"""Focused unit coverage for read-only Control Rig VMD export routing."""

from types import SimpleNamespace
import unittest
from unittest import mock

from mmd_tools.core.constants import ATTR_MMD_BONE_NAME
from mmd_tools.core.mmd_control_rig_builder import (
    CONTROL_RIG_CONTROL_OWNED,
    CONTROL_RIG_EDIT,
    MmdControlRigBuildError,
)
from mmd_tools.core.mmd_control_rig_motion import (
    resolve_control_rig_direct_vmd_export_routes,
)


class _DirectExportCmds:
    """Small read-only Maya command surface used by the route resolver."""

    def __init__(self):
        self.nodes_by_uuid = {
            "root-uuid": "|model",
            "joint-a-uuid": "|model|jointA",
            "joint-b-uuid": "|model|jointB",
            "control-a-uuid": "|model|controls|controlA",
            "control-b-uuid": "|model|controls|controlB",
        }
        self.descendants = ("|model|jointA", "|model|jointB")
        self.bone_names = {
            "|model|jointA": "上半身",
            "|model|jointB": "上半身2",
        }
        self.ik_names = {}
        self.custom_attrs = {}
        self.incoming = {}
        self.node_types = {}

    def ls(self, value=None, **_kwargs):
        if value in self.nodes_by_uuid:
            return [self.nodes_by_uuid[value]]
        known = set(self.nodes_by_uuid.values())
        return [value] if value in known else []

    def listRelatives(self, node, **_kwargs):
        return list(self.descendants) if node == "|model" else []

    def attributeQuery(self, attribute, *, node, exists=False):
        assert exists
        if attribute == ATTR_MMD_BONE_NAME:
            return node in self.bone_names
        if attribute == "mmd_ik_bone_name":
            return node in self.ik_names
        return (node, attribute) in self.custom_attrs

    def getAttr(self, plug):
        node, attribute = plug.rsplit(".", 1)
        if attribute == ATTR_MMD_BONE_NAME:
            return self.bone_names.get(node)
        if attribute == "mmd_ik_bone_name":
            return self.ik_names.get(node)
        if (node, attribute) in self.custom_attrs:
            return self.custom_attrs[(node, attribute)]
        raise AssertionError(f"unexpected getAttr: {plug}")

    def listConnections(self, target, **_kwargs):
        return list(self.incoming.get(target, ()))

    def nodeType(self, node):
        return self.node_types.get(node, "transform")


def _binding(
    joint_uuid,
    node_uuid,
    *,
    fallback=None,
    channels=None,
    input_kind="direct_channel",
    ik_solver_uuids=None,
):
    channels = channels or ("rotateX", "rotateY", "rotateZ")
    return {
        "jointUuid": joint_uuid,
        "inputKind": input_kind,
        "authoredPlugs": [f"joint.{channel}" for channel in channels],
        "authoredPlugRefs": [
            {"nodeUuid": node_uuid, "attribute": channel} for channel in channels
        ],
        "fallback": fallback,
        **(
            {"ikSolverUuids": list(ik_solver_uuids)}
            if ik_solver_uuids is not None
            else {}
        ),
    }


def _metadata(bindings):
    return {
        "state": CONTROL_RIG_EDIT,
        "owner": CONTROL_RIG_CONTROL_OWNED,
        "bindings": bindings,
        "nodes": [],
        "journal": {"channels": [], "ikEnabled": [], "offsetParentMatrix": []},
    }


def _journal_rows(control, joint, channels=("rotateX", "rotateY", "rotateZ")):
    return [
        {"control": f"{control}.{channel}", "target": f"{joint}.{channel}"}
        for channel in channels
    ]


def _resolve(cmds, metadata, controls, journal_rows, ik_rows=None):
    rig = SimpleNamespace(model_root="|model", controls=controls)
    module = "mmd_tools.core.mmd_control_rig_motion"
    with (
        mock.patch(f"{module}.read_mmd_control_rig_metadata", return_value=metadata),
        mock.patch(f"{module}.inspect_mmd_control_rig", return_value=rig),
        mock.patch(
            f"{module}._resolve_edit_journal",
            return_value=(ik_rows or [], journal_rows, []),
        ),
    ):
        return resolve_control_rig_direct_vmd_export_routes(
            "|model", cmds_module=cmds
        )


def _connect_direct_value_routes(cmds, control, joint, channels):
    for channel in channels:
        cmds.incoming[f"{joint}.{channel}"] = (f"{control}.{channel}",)


class TestControlRigDirectVmdExport(unittest.TestCase):
    def test_resolves_dedicated_candidate_with_control_selectors_and_joint_values(self):
        cmds = _DirectExportCmds()
        control = "|model|controls|controlA"
        joint = "|model|jointA"
        channels = ("rotateX", "rotateY", "rotateZ")
        _connect_direct_value_routes(cmds, control, joint, channels)

        result = _resolve(
            cmds,
            _metadata({"upper_body": _binding("joint-a-uuid", "joint-a-uuid")}),
            {"upper_body": control},
            _journal_rows(control, joint),
        )

        candidate = result["candidates"][joint]
        self.assertEqual(candidate["boneName"], "上半身")
        self.assertEqual(
            candidate["selectorPlugs"],
            tuple(f"{control}.{channel}" for channel in channels),
        )
        self.assertEqual(
            candidate["valueRoutes"],
            {channel: (joint, channel) for channel in channels},
        )
        self.assertEqual(candidate["ownedFamilies"], ("rotate",))
        self.assertEqual(result["omittedRoles"], ())
    def test_omits_fallback_binding_without_resolving_an_alias(self):
        cmds = _DirectExportCmds()

        result = _resolve(
            cmds,
            _metadata(
                {
                    "upper_body2": _binding(
                        "missing-joint-uuid",
                        "missing-joint-uuid",
                        fallback="upper_body",
                    )
                }
            ),
            {},
            [],
        )

        self.assertEqual(result["candidates"], {})
        self.assertEqual(
            result["omittedRoles"],
            ({"role": "upper_body2", "reason": "fallback"},),
        )
    def test_missing_dedicated_control_fails_closed(self):
        cmds = _DirectExportCmds()
        metadata = _metadata(
            {"upper_body": _binding("joint-a-uuid", "joint-a-uuid")}
        )

        with self.assertRaisesRegex(MmdControlRigBuildError, "missing owned control"):
            _resolve(cmds, metadata, {}, [])
    def test_duplicate_vmd_bone_name_fails_closed(self):
        cmds = _DirectExportCmds()
        cmds.bone_names["|model|jointB"] = "上半身"
        control_a = "|model|controls|controlA"
        control_b = "|model|controls|controlB"
        joint_a = "|model|jointA"
        joint_b = "|model|jointB"
        channels = ("rotateX", "rotateY", "rotateZ")
        _connect_direct_value_routes(cmds, control_a, joint_a, channels)
        _connect_direct_value_routes(cmds, control_b, joint_b, channels)
        metadata = _metadata(
            {
                "upper_body": _binding("joint-a-uuid", "joint-a-uuid"),
                "upper_body2": _binding("joint-b-uuid", "joint-b-uuid"),
            }
        )
        rows = _journal_rows(control_a, joint_a) + _journal_rows(control_b, joint_b)

        with self.assertRaisesRegex(MmdControlRigBuildError, "claim VMD bone name"):
            _resolve(
                cmds,
                metadata,
                {"upper_body": control_a, "upper_body2": control_b},
                rows,
            )
    def test_foreign_authored_writer_fails_closed(self):
        cmds = _DirectExportCmds()
        control = "|model|controls|controlA"
        joint = "|model|jointA"
        channels = ("rotateX", "rotateY", "rotateZ")
        _connect_direct_value_routes(cmds, control, joint, channels)
        cmds.incoming[f"{joint}.rotateY"] = ("foreignNode.output",)
        cmds.node_types["foreignNode"] = "multiplyDivide"

        with self.assertRaisesRegex(
            MmdControlRigBuildError, "unknown Control Rig writer"
        ):
            _resolve(
                cmds,
                _metadata(
                    {"upper_body": _binding("joint-a-uuid", "joint-a-uuid")}
                ),
                {"upper_body": control},
                _journal_rows(control, joint),
            )
    def test_accepts_uuid_owned_translate_baseline_from_edit_journal(self):
        cmds = _DirectExportCmds()
        control = "|model|controls|controlA"
        joint = "|model|jointA"
        baseline = "center_TRANSLATE_BASELINE"
        channels = ("translateX", "translateY", "translateZ")
        cmds.nodes_by_uuid["baseline-uuid"] = baseline
        cmds.node_types[baseline] = "plusMinusAverage"
        cmds.incoming[f"{joint}.translateX"] = (f"{baseline}.output1D",)
        cmds.incoming[baseline] = (f"{control}.translateX",)
        for channel in channels[1:]:
            cmds.incoming[f"{joint}.{channel}"] = (f"{control}.{channel}",)
        metadata = _metadata(
            {
                "center": _binding(
                    "joint-a-uuid",
                    "joint-a-uuid",
                    channels=channels,
                )
            }
        )
        metadata["journal"]["channels"] = [
            {"translateBaselineOutputRef": {"nodeUuid": "baseline-uuid"}}
        ]

        result = _resolve(
            cmds,
            metadata,
            {"center": control},
            _journal_rows(control, joint, channels),
        )

        self.assertEqual(
            result["candidates"][joint]["valueRoutes"]["translateX"],
            (joint, "translateX"),
        )

    def test_uses_owned_pre_morph_value_when_authored_plug_is_internal(self):
        cmds = _DirectExportCmds()
        control = "|model|controls|controlA"
        joint = "|model|jointA"
        accum = "center_boneMorphAccum"
        channels = ("translateX", "translateY", "translateZ")
        cmds.nodes_by_uuid["accum-uuid"] = accum
        cmds.node_types[accum] = "mmdBoneMorphAccum"
        authored_channels = tuple(f"baseTranslate{axis}" for axis in "XYZ")
        for control_channel, authored_channel in zip(channels, authored_channels):
            cmds.incoming[f"{accum}.{authored_channel}"] = (
                f"{control}.{control_channel}",
            )
        binding = _binding(
            "joint-a-uuid",
            "accum-uuid",
            channels=authored_channels,
        )
        rows = [
            {
                "control": f"{control}.{control_channel}",
                "target": f"{accum}.{authored_channel}",
            }
            for control_channel, authored_channel in zip(channels, authored_channels)
        ]

        result = _resolve(
            cmds,
            _metadata({"center": binding}),
            {"center": control},
            rows,
        )

        self.assertEqual(
            result["candidates"][joint]["valueRoutes"],
            {
                control_channel: (accum, authored_channel)
                for control_channel, authored_channel in zip(
                    channels, authored_channels
                )
            },
        )

    def test_rotate_selector_keeps_pre_morph_translate_value_family(self):
        cmds = _DirectExportCmds()
        control = "|model|controls|controlA"
        joint = "|model|jointA"
        accum = "upper_body_boneMorphAccum"
        cmds.nodes_by_uuid["accum-uuid"] = accum
        cmds.node_types[accum] = "mmdBoneMorphAccum"
        authored_channels = tuple(
            f"base{family}{axis}"
            for family in ("Translate", "Rotate")
            for axis in "XYZ"
        )
        rotate_channels = ("rotateX", "rotateY", "rotateZ")
        rotate_targets = tuple(f"baseRotate{axis}" for axis in "XYZ")
        for control_channel, target_channel in zip(
            rotate_channels, rotate_targets
        ):
            cmds.incoming[f"{accum}.{target_channel}"] = (
                f"{control}.{control_channel}",
            )
        rows = [
            {
                "control": f"{control}.{control_channel}",
                "target": f"{accum}.{target_channel}",
            }
            for control_channel, target_channel in zip(
                rotate_channels, rotate_targets
            )
        ]

        result = _resolve(
            cmds,
            _metadata(
                {
                    "upper_body": _binding(
                        "joint-a-uuid",
                        "accum-uuid",
                        channels=authored_channels,
                        input_kind="bone_morph_base",
                    )
                }
            ),
            {"upper_body": control},
            rows,
        )

        self.assertEqual(
            set(result["candidates"][joint]["valueRoutes"]),
            {
                "translateX",
                "translateY",
                "translateZ",
                "rotateX",
                "rotateY",
                "rotateZ",
            },
        )

    def test_unowned_family_accepts_uuid_owned_authoring_helper(self):
        cmds = _DirectExportCmds()
        control = "|model|controls|controlA"
        joint = "|model|jointA"
        accum = "upper_body_boneMorphAccum"
        helper = "upper_body_vmdAuthoring"
        curve = "upper_body_translateX_curve"
        cmds.nodes_by_uuid.update(
            {"accum-uuid": accum, "helper-uuid": helper}
        )
        cmds.node_types.update(
            {
                accum: "mmdBoneMorphAccum",
                helper: "transform",
                curve: "animCurveTL",
            }
        )
        authored_channels = tuple(
            f"base{family}{axis}"
            for family in ("Translate", "Rotate")
            for axis in "XYZ"
        )
        cmds.incoming[f"{accum}.baseTranslateX"] = (f"{helper}.translateX",)
        cmds.incoming[f"{helper}.translateX"] = (f"{curve}.output",)
        rotate_channels = ("rotateX", "rotateY", "rotateZ")
        rotate_targets = tuple(f"baseRotate{axis}" for axis in "XYZ")
        for control_channel, target_channel in zip(
            rotate_channels, rotate_targets
        ):
            cmds.incoming[f"{accum}.{target_channel}"] = (
                f"{control}.{control_channel}",
            )
        metadata = _metadata(
            {
                "upper_body": _binding(
                    "joint-a-uuid",
                    "accum-uuid",
                    channels=authored_channels,
                    input_kind="bone_morph_base",
                )
            }
        )
        marker = "mmd_vmd_authoring_proxy"
        target_attribute = "mmd_vmd_authoring_target"
        cmds.custom_attrs[(helper, marker)] = True
        cmds.custom_attrs[(helper, target_attribute)] = None
        cmds.incoming[f"{helper}.{target_attribute}"] = (f"{joint}.message",)

        result = _resolve(
            cmds,
            metadata,
            {"upper_body": control},
            [
                {
                    "control": f"{control}.{control_channel}",
                    "target": f"{accum}.{target_channel}",
                }
                for control_channel, target_channel in zip(
                    rotate_channels, rotate_targets
                )
            ],
        )

        self.assertEqual(
            result["candidates"][joint]["valueRoutes"]["translateX"],
            (accum, "baseTranslateX"),
        )
    def test_accepts_journal_validated_animation_layer_output(self):
        cmds = _DirectExportCmds()
        control = "|model|controls|controlA"
        joint = "|model|jointA"
        channels = ("rotateX", "rotateY", "rotateZ")
        rows = _journal_rows(control, joint, channels)
        rows[0]["layerRoute"] = {
            "blend": "upperBodyLayer.inputBX",
            "blendOutput": "upperBodyLayer.outputX",
        }
        cmds.node_types["upperBodyLayer"] = "animBlendNodeAdditiveRotation"
        cmds.incoming[f"{joint}.rotateX"] = ("upperBodyLayer.outputX",)
        cmds.incoming["upperBodyLayer.inputBX"] = (f"{control}.rotateX",)
        for channel in channels[1:]:
            cmds.incoming[f"{joint}.{channel}"] = (f"{control}.{channel}",)

        result = _resolve(
            cmds,
            _metadata({"upper_body": _binding("joint-a-uuid", "joint-a-uuid")}),
            {"upper_body": control},
            rows,
        )

        self.assertEqual(result["candidates"][joint]["boneName"], "上半身")
    def test_partial_channel_family_fails_closed(self):
        cmds = _DirectExportCmds()
        control = "|model|controls|controlA"
        joint = "|model|jointA"
        channels = ("rotateX", "rotateY")
        _connect_direct_value_routes(cmds, control, joint, channels)

        with self.assertRaisesRegex(
            MmdControlRigBuildError, "partial Control Rig selector family"
        ):
            _resolve(
                cmds,
                _metadata(
                    {
                        "upper_body": _binding(
                            "joint-a-uuid", "joint-a-uuid", channels=channels
                        )
                    }
                ),
                {"upper_body": control},
                _journal_rows(control, joint, channels),
            )

    def test_resolves_control_side_ik_state_route_from_owned_solver_journal(self):
        cmds = _DirectExportCmds()
        control = "|model|controls|controlA"
        joint = "|model|jointA"
        solver = "left_leg_ik_mmdCcdIk"
        channels = ("translateX", "translateY", "translateZ")
        cmds.nodes_by_uuid["solver-uuid"] = solver
        cmds.ik_names[solver] = "左足ＩＫ"
        _connect_direct_value_routes(cmds, control, joint, channels)
        cmds.incoming[f"{solver}.enabled"] = (f"{control}.ikEnabled",)
        binding = _binding(
            "joint-a-uuid",
            "joint-a-uuid",
            channels=channels,
            input_kind="ik_controller",
            ik_solver_uuids=("solver-uuid",),
        )

        result = _resolve(
            cmds,
            _metadata({"left_foot_ik": binding}),
            {"left_foot_ik": control},
            _journal_rows(control, joint, channels),
            ik_rows=[
                {
                    "control": f"{control}.ikEnabled",
                    "target": f"{solver}.enabled",
                }
            ],
        )

        self.assertEqual(
            result["ikStateRoutes"],
            {"左足ＩＫ": (control, "ikEnabled")},
        )
