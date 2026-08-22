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
        return attribute == ATTR_MMD_BONE_NAME and node in self.bone_names

    def getAttr(self, plug):
        node, attribute = plug.rsplit(".", 1)
        if attribute == ATTR_MMD_BONE_NAME:
            return self.bone_names.get(node)
        raise AssertionError(f"unexpected getAttr: {plug}")

    def listConnections(self, target, **_kwargs):
        return list(self.incoming.get(target, ()))

    def nodeType(self, node):
        return self.node_types.get(node, "transform")


def _binding(joint_uuid, node_uuid, *, fallback=None, channels=None):
    channels = channels or ("rotateX", "rotateY", "rotateZ")
    return {
        "jointUuid": joint_uuid,
        "inputKind": "direct_channel",
        "authoredPlugs": [f"joint.{channel}" for channel in channels],
        "authoredPlugRefs": [
            {"nodeUuid": node_uuid, "attribute": channel} for channel in channels
        ],
        "fallback": fallback,
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


def _resolve(cmds, metadata, controls, journal_rows):
    rig = SimpleNamespace(model_root="|model", controls=controls)
    module = "mmd_tools.core.mmd_control_rig_motion"
    with (
        mock.patch(f"{module}.read_mmd_control_rig_metadata", return_value=metadata),
        mock.patch(f"{module}.inspect_mmd_control_rig", return_value=rig),
        mock.patch(
            f"{module}._resolve_edit_journal",
            return_value=([], journal_rows, []),
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
