"""Focused tests for read-only Control Rig VMD route selection."""

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


class _Cmds:
    def __init__(self):
        self.nodes = {
            "joint-a": "|model|jointA",
            "joint-b": "|model|jointB",
            "control-a": "|model|controls|controlA",
            "control-b": "|model|controls|controlB",
        }
        self.bone_names = {
            "|model|jointA": "上半身",
            "|model|jointB": "上半身2",
        }
        self.ik_names = {}
        self.incoming = {}

    def ls(self, value=None, **_kwargs):
        value = self.nodes.get(value, value)
        return [value] if value in self.nodes.values() or value == "|model" else []

    def listRelatives(self, node, **_kwargs):
        return ["|model|jointA", "|model|jointB"] if node == "|model" else []

    def attributeQuery(self, attribute, *, node, exists=False):
        assert exists
        if attribute == ATTR_MMD_BONE_NAME:
            return node in self.bone_names
        return attribute == "mmd_ik_bone_name" and node in self.ik_names

    def getAttr(self, plug):
        node, attribute = plug.rsplit(".", 1)
        if attribute == ATTR_MMD_BONE_NAME:
            return self.bone_names.get(node)
        if attribute == "mmd_ik_bone_name":
            return self.ik_names.get(node)
        raise AssertionError(plug)

    def listConnections(self, plug, **_kwargs):
        return list(self.incoming.get(plug, ()))


def _binding(
    joint_uuid,
    node_uuid=None,
    *,
    channels=("rotateX", "rotateY", "rotateZ"),
    fallback=None,
    input_kind="direct_channel",
    ik_solver_uuids=(),
):
    node_uuid = node_uuid or joint_uuid
    return {
        "jointUuid": joint_uuid,
        "inputKind": input_kind,
        "authoredPlugs": [f"joint.{channel}" for channel in channels],
        "authoredPlugRefs": [
            {"nodeUuid": node_uuid, "attribute": channel} for channel in channels
        ],
        "fallback": fallback,
        "ikSolverUuids": list(ik_solver_uuids),
    }


def _metadata(bindings, channel_rows=(), ik_rows=()):
    return {
        "state": CONTROL_RIG_EDIT,
        "owner": CONTROL_RIG_CONTROL_OWNED,
        "bindings": bindings,
        "journal": {
            "channels": list(channel_rows),
            "ikEnabled": list(ik_rows),
            "offsetParentMatrix": [],
        },
    }


def _rows(control, target, channels):
    return [
        {"control": f"{control}.{channel}", "target": f"{target}.{channel}"}
        for channel in channels
    ]


def _resolve(cmds, metadata, controls):
    for row in metadata["journal"]["channels"]:
        cmds.incoming.setdefault(row["target"], (row["control"],))
    rig = SimpleNamespace(model_root="|model", controls=controls)
    module = "mmd_tools.core.mmd_control_rig_motion"
    with (
        mock.patch(f"{module}.read_mmd_control_rig_metadata", return_value=metadata),
        mock.patch(f"{module}.inspect_mmd_control_rig", return_value=rig),
        mock.patch(
            f"{module}._resolve_edit_journal",
            return_value=(
                metadata["journal"]["ikEnabled"],
                metadata["journal"]["channels"],
                [],
            ),
        ),
    ):
        return resolve_control_rig_direct_vmd_export_routes("|model", cmds_module=cmds)


class TestControlRigDirectVmdExport(unittest.TestCase):
    def test_uses_control_as_selector_and_mmd_joint_as_value(self):
        cmds = _Cmds()
        control = "|model|controls|controlA"
        joint = "|model|jointA"
        channels = ("rotateX", "rotateY", "rotateZ")
        result = _resolve(
            cmds,
            _metadata(
                {"upper_body": _binding("joint-a")},
                _rows(control, joint, channels),
            ),
            {"upper_body": control},
        )

        self.assertEqual(
            result["candidates"][joint],
            {
                "boneName": "上半身",
                "selectorPlugs": tuple(f"{control}.{channel}" for channel in channels),
                "valueRoutes": {channel: (joint, channel) for channel in channels},
            },
        )

    def test_both_eyes_metadata_binding_is_a_direct_export_candidate(self):
        cmds = _Cmds()
        control = "|model|controls|controlA"
        joint = "|model|jointA"
        cmds.bone_names[joint] = "両目"
        channels = ("rotateX", "rotateY", "rotateZ")

        result = _resolve(
            cmds,
            _metadata(
                {"both_eyes": _binding("joint-a")},
                _rows(control, joint, channels),
            ),
            {"both_eyes": control},
        )

        self.assertEqual(
            result["candidates"][joint],
            {
                "boneName": "両目",
                "selectorPlugs": tuple(f"{control}.{channel}" for channel in channels),
                "valueRoutes": {channel: (joint, channel) for channel in channels},
            },
        )

    def test_omits_fallback_binding(self):
        result = _resolve(
            _Cmds(),
            _metadata({"upper_body2": _binding("missing", fallback="upper_body")}),
            {},
        )
        self.assertEqual(result["candidates"], {})

    def test_rejects_missing_control_and_duplicate_vmd_name(self):
        with self.assertRaisesRegex(MmdControlRigBuildError, "missing owned control"):
            _resolve(
                _Cmds(),
                _metadata({"upper_body": _binding("joint-a")}),
                {},
            )

        cmds = _Cmds()
        cmds.bone_names["|model|jointB"] = "上半身"
        control_a = "|model|controls|controlA"
        control_b = "|model|controls|controlB"
        channels = ("rotateX", "rotateY", "rotateZ")
        rows = _rows(control_a, "|model|jointA", channels)
        rows += _rows(control_b, "|model|jointB", channels)
        with self.assertRaisesRegex(MmdControlRigBuildError, "claim VMD bone name"):
            _resolve(
                cmds,
                _metadata(
                    {
                        "upper_body": _binding("joint-a"),
                        "upper_body2": _binding("joint-b"),
                    },
                    rows,
                ),
                {"upper_body": control_a, "upper_body2": control_b},
            )

    def test_keeps_pre_morph_value_families(self):
        cmds = _Cmds()
        control = "|model|controls|controlA"
        accum = "upper_body_boneMorphAccum"
        cmds.nodes["accum"] = accum
        authored = tuple(
            f"base{family}{axis}"
            for family in ("Translate", "Rotate")
            for axis in "XYZ"
        )
        rows = [
            {
                "control": f"{control}.rotate{axis}",
                "target": f"{accum}.baseRotate{axis}",
            }
            for axis in "XYZ"
        ]
        result = _resolve(
            cmds,
            _metadata(
                {
                    "upper_body": _binding(
                        "joint-a",
                        "accum",
                        channels=authored,
                        input_kind="bone_morph_base",
                    )
                },
                rows,
            ),
            {"upper_body": control},
        )
        self.assertEqual(
            set(result["candidates"]["|model|jointA"]["valueRoutes"]),
            {f"{family}{axis}" for family in ("translate", "rotate") for axis in "XYZ"},
        )

    def test_rejects_partial_family(self):
        cmds = _Cmds()
        control = "|model|controls|controlA"
        channels = ("rotateX", "rotateY")
        with self.assertRaisesRegex(MmdControlRigBuildError, "partial"):
            _resolve(
                cmds,
                _metadata(
                    {"upper_body": _binding("joint-a", channels=channels)},
                    _rows(control, "|model|jointA", channels),
                ),
                {"upper_body": control},
            )

    def test_uses_control_ik_state_for_solver_vmd_name(self):
        cmds = _Cmds()
        control = "|model|controls|controlA"
        joint = "|model|jointA"
        solver = "left_leg_ik_mmdCcdIk"
        cmds.nodes["solver"] = solver
        cmds.ik_names[solver] = "左足ＩＫ"
        cmds.incoming[f"{solver}.enabled"] = (f"{control}.ikEnabled",)
        channels = ("translateX", "translateY", "translateZ")
        result = _resolve(
            cmds,
            _metadata(
                {
                    "left_foot_ik": _binding(
                        "joint-a",
                        channels=channels,
                        input_kind="ik_controller",
                        ik_solver_uuids=("solver",),
                    )
                },
                _rows(control, joint, channels),
                [{"control": f"{control}.ikEnabled", "target": f"{solver}.enabled"}],
            ),
            {"left_foot_ik": control},
        )
        self.assertEqual(result["ikStateRoutes"], {"左足ＩＫ": (control, "ikEnabled")})


if __name__ == "__main__":
    unittest.main()
