"""Unit coverage for read-only UUID-owned Control Rig inspection."""

import copy
import json
import unittest

from mmd_tools.core.mmd_control_rig_builder import (
    CONTROL_RIG_ATTACHED,
    CONTROL_RIG_CONTROL_OWNED,
    CONTROL_RIG_EDIT,
    CONTROL_RIG_METADATA_SCHEMA,
    CONTROL_RIG_METADATA_VERSION,
    CONTROL_RIG_MMD_OWNED,
    MmdControlRigBuildError,
    build_mmd_control_rig,
    inspect_mmd_control_rig,
)
from mmd_tools.core.constants import ATTR_MMD_CONTROL_RIG_JSON


class _InspectionCmds:
    def __init__(self, *, metadata=None, root_uuid="root-uuid"):
        self.root = "|char:model"
        self.root_uuid = root_uuid
        self.nodes_by_uuid = {
            "root-uuid": self.root,
            "group-uuid": "|char:model|Controls",
            "set-uuid": "Controls_SET",
            "ctrl-uuid": "|char:model|Controls|center_CTRL",
            "zero-uuid": "|char:model|Controls|center_ZERO",
        }
        self.metadata = metadata if metadata is not None else self._metadata()
        self.calls = []

    def _metadata(self):
        return {
            "schema": CONTROL_RIG_METADATA_SCHEMA,
            "version": CONTROL_RIG_METADATA_VERSION,
            "state": CONTROL_RIG_ATTACHED,
            "owner": CONTROL_RIG_MMD_OWNED,
            "displayReferenceTime": 0.0,
            "modelRootUuid": "root-uuid",
            "controlGroupUuid": "group-uuid",
            "selectionSetUuid": "set-uuid",
            "nodes": [
                {"uuid": uuid, "name": node}
                for uuid, node in self.nodes_by_uuid.items()
                if uuid != "root-uuid"
            ],
            "controls": {"center": "ctrl-uuid"},
            "zeroGroups": {"center": "zero-uuid"},
        }

    def ls(self, value=None, *, long=False, uuid=False):
        self.calls.append(("ls", value, long, uuid))
        if uuid:
            return [self.root_uuid] if value == self.root else []
        if value in self.nodes_by_uuid:
            return [self.nodes_by_uuid[value]]
        if value == "model" or value == self.root:
            return [self.root]
        return []

    def attributeQuery(self, attribute, *, node, exists=False):
        return exists and attribute == ATTR_MMD_CONTROL_RIG_JSON and node == self.root

    def getAttr(self, plug):
        assert plug == f"{self.root}.{ATTR_MMD_CONTROL_RIG_JSON}"
        return json.dumps(self.metadata)

    def listRelatives(self, node, *, allDescendents=False, fullPath=False):
        self.calls.append(("listRelatives", node, allDescendents, fullPath))
        if node == self.nodes_by_uuid["group-uuid"]:
            return [
                self.nodes_by_uuid["zero-uuid"],
                self.nodes_by_uuid["ctrl-uuid"],
            ]
        return []


class TestMmdControlRigInspection(unittest.TestCase):
    def test_inspection_resolves_canonical_root_without_mutation(self):
        cmds = _InspectionCmds()

        result = inspect_mmd_control_rig("model", cmds_module=cmds)

        self.assertIsNotNone(result)
        self.assertEqual(result.model_root, "|char:model")
        self.assertEqual(result.control_group, "|char:model|Controls")
        self.assertFalse(result.created)
        self.assertTrue(all(call[0] in {"ls", "listRelatives"} for call in cmds.calls))

    def test_missing_metadata_returns_none(self):
        cmds = _InspectionCmds(metadata=None)
        cmds.metadata = None
        cmds.attributeQuery = lambda *_args, **_kwargs: False

        self.assertIsNone(inspect_mmd_control_rig("model", cmds_module=cmds))

    def test_model_uuid_mismatch_fails_closed(self):
        cmds = _InspectionCmds(root_uuid="different-root")
        with self.assertRaisesRegex(MmdControlRigBuildError, "model UUID mismatch"):
            inspect_mmd_control_rig("model", cmds_module=cmds)

    def test_unrecorded_uuid_fails_closed(self):
        cmds = _InspectionCmds()
        cmds.metadata["controls"]["center"] = "not-recorded"
        with self.assertRaisesRegex(MmdControlRigBuildError, "unrecorded"):
            inspect_mmd_control_rig("model", cmds_module=cmds)

    def test_missing_uuid_resolution_fails_closed(self):
        cmds = _InspectionCmds()
        del cmds.nodes_by_uuid["ctrl-uuid"]
        with self.assertRaisesRegex(MmdControlRigBuildError, "missing"):
            inspect_mmd_control_rig("model", cmds_module=cmds)

    def test_ambiguous_uuid_resolution_fails_closed(self):
        cmds = _InspectionCmds()
        original_ls = cmds.ls

        def ambiguous(value=None, **kwargs):
            if value == "group-uuid" and kwargs.get("long"):
                return ["|char:model|Controls", "|other:model|Controls"]
            return original_ls(value, **kwargs)

        cmds.ls = ambiguous
        with self.assertRaisesRegex(MmdControlRigBuildError, "missing"):
            inspect_mmd_control_rig("model", cmds_module=cmds)

    def test_moved_recorded_node_fails_topology_validation(self):
        cmds = _InspectionCmds()
        original = cmds.listRelatives
        cmds.listRelatives = lambda *args, **kwargs: []

        with self.assertRaisesRegex(MmdControlRigBuildError, "topology changed"):
            inspect_mmd_control_rig("model", cmds_module=cmds)
        cmds.listRelatives = original

    def test_foreign_descendant_fails_topology_validation(self):
        cmds = _InspectionCmds()
        original = cmds.listRelatives

        def foreign(*args, **kwargs):
            return [*original(*args, **kwargs), "|char:model|Controls|foreign"]

        cmds.listRelatives = foreign
        with self.assertRaisesRegex(MmdControlRigBuildError, "topology changed"):
            inspect_mmd_control_rig("model", cmds_module=cmds)

    def test_existing_attached_rig_repairs_legacy_channel_state_idempotently(self):
        cmds = _MigrationCmds()

        first = build_mmd_control_rig("model", cmds_module=cmds)
        first_state = copy.deepcopy(cmds.channel_state)
        calls_after_first = len(cmds.set_calls)
        second = build_mmd_control_rig("model", cmds_module=cmds)

        self.assertFalse(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first_state, cmds.channel_state)
        self.assertEqual(len(cmds.set_calls), calls_after_first)
        for channel in ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ"):
            self.assertTrue(cmds.channel_state[f"{cmds.control}.{channel}"]["keyable"])
        for channel in ("scaleX", "scaleY", "scaleZ", "visibility"):
            state = cmds.channel_state[f"{cmds.control}.{channel}"]
            self.assertFalse(state["keyable"])
            self.assertTrue(state["locked"])

    def test_existing_edit_control_owned_rig_is_not_mutated(self):
        cmds = _MigrationCmds(state=CONTROL_RIG_EDIT)
        before = copy.deepcopy(cmds.channel_state)

        result = build_mmd_control_rig("model", cmds_module=cmds)

        self.assertFalse(result.created)
        self.assertEqual(before, cmds.channel_state)
        self.assertEqual(cmds.set_calls, [])

    def test_existing_rig_migration_failure_restores_channel_state(self):
        cmds = _MigrationCmds(fail_migration=True)
        before = copy.deepcopy(cmds.channel_state)

        with self.assertRaisesRegex(RuntimeError, "simulated migration failure"):
            build_mmd_control_rig("model", cmds_module=cmds)

        self.assertEqual(cmds.channel_state, before)


class _MigrationCmds(_InspectionCmds):
    """Small Maya-cmds double for existing-rig channel migration tests."""

    control = "|char:model|Controls|center_CTRL"

    def __init__(self, *, state=CONTROL_RIG_ATTACHED, fail_migration=False):
        super().__init__()
        self.metadata["state"] = state
        self.metadata["owner"] = (
            CONTROL_RIG_CONTROL_OWNED
            if state == CONTROL_RIG_EDIT
            else CONTROL_RIG_MMD_OWNED
        )
        self.metadata["controls"] = {
            "center": "ctrl-uuid",
            "groove": "ctrl-uuid",
        }
        self.metadata["bindings"] = {
            role: {
                "joint": "|model|joint",
                "inputKind": "direct_channel",
                "authoredPlugs": ("|model|joint.translate", "|model|joint.rotate"),
                "fallback": "center" if role == "groove" else None,
            }
            for role in ("center", "groove")
        }
        self.channel_state = {
            f"{self.control}.{channel}": {
                "locked": False,
                "keyable": True,
                "channelBox": True,
            }
            for channel in (
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
        }
        self.set_calls = []
        self.fail_migration = fail_migration

    def getAttr(self, plug, **kwargs):
        if kwargs:
            state = self.channel_state[plug]
            if kwargs.get("lock"):
                return state["locked"]
            if kwargs.get("keyable"):
                return state["keyable"]
            if kwargs.get("channelBox"):
                return state["channelBox"]
        return super().getAttr(plug)

    def setAttr(self, plug, **kwargs):
        self.set_calls.append((plug, dict(kwargs)))
        if self.fail_migration and plug.endswith(".rotateX") and kwargs.get("keyable"):
            self.fail_migration = False
            raise RuntimeError("simulated migration failure")
        state = self.channel_state[plug]
        for source, target in (("lock", "locked"), ("keyable", "keyable"), ("channelBox", "channelBox")):
            if source in kwargs:
                state[target] = bool(kwargs[source])

    def undoInfo(self, **kwargs):
        self.calls.append(("undoInfo", kwargs))


if __name__ == "__main__":
    unittest.main()
