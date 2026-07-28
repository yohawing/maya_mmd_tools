"""Unit coverage for read-only UUID-owned Control Rig inspection."""

import json
import unittest

from mmd_tools.core.mmd_control_rig_builder import (
    CONTROL_RIG_ATTACHED,
    CONTROL_RIG_METADATA_SCHEMA,
    CONTROL_RIG_METADATA_VERSION,
    CONTROL_RIG_MMD_OWNED,
    MmdControlRigBuildError,
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


if __name__ == "__main__":
    unittest.main()
