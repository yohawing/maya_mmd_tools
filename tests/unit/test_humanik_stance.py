"""Host-neutral tests for automatic canonical-world HumanIK stance handling."""

import json
import unittest

from mmd_tools.core.humanik_resolver import HumanIkBoneAssignment
from mmd_tools.core.humanik_stance import (
    HumanIkStanceTransaction,
    canonical_stance_targets,
    direction_evidence,
    joint_world_direction,
)


def _matrix(x, y, z, *, scale_x=1.0):
    return [scale_x, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, x, y, z, 1.0]


def _assignment(hik_bone, joint):
    return HumanIkBoneAssignment(
        joint=joint,
        mmd_bone=hik_bone,
        hik_bone=hik_bone,
        hik_index=1,
        source="test",
    )


class _FakeCmds:
    def __init__(self, *, left_direction=1.0):
        self.matrices = {
            "|model|left_arm": _matrix(0.0, 1.0, 0.0),
            "|model|left_forearm": _matrix(left_direction, 1.0, 0.0),
            "|model|right_arm": _matrix(0.0, 1.0, 0.0),
            "|model|right_forearm": _matrix(-1.0, 1.0, 0.0),
        }
        self.attrs = {}
        self.connections = {}
        self.calls = []
        for joint in self.matrices:
            self.attrs[f"{joint}.rotate"] = [(0.0, 0.0, 0.0)]
            self.attrs[f"{joint}.jointOrient"] = [(0.0, 0.0, 0.0)]
            self.attrs[f"{joint}.rotateAxis"] = [(0.0, 0.0, 0.0)]
            self.attrs[f"{joint}.translate"] = [(0.0, 0.0, 0.0)]
            self.attrs[f"{joint}.scale"] = [(1.0, 1.0, 1.0)]

    def getAttr(self, plug, **kwargs):
        self.calls.append(("getAttr", plug, kwargs))
        if kwargs.get("multiIndices"):
            return []
        if plug.endswith("worldMatrix[0]"):
            return self.matrices[plug.rsplit(".worldMatrix", 1)[0]]
        return self.attrs.get(plug, 0.0)

    def setAttr(self, plug, *values, **kwargs):
        self.calls.append(("setAttr", plug, values, kwargs))
        if len(values) == 3:
            self.attrs[plug] = [tuple(float(value) for value in values)]
        elif values:
            self.attrs[plug] = values[0]
        if plug.endswith(".rotate"):
            restore = getattr(self, "_stance_restore_matrices", {}).get(plug.rsplit(".rotate", 1)[0])
            if restore is not None:
                child, matrix = restore
                self.matrices[child] = list(matrix)

    def listRelatives(self, *args, **kwargs):
        return []

    def listHistory(self, *args, **kwargs):
        return []

    def nodeType(self, node):
        return "joint"

    def ls(self, node=None, **kwargs):
        return [node] if node else []

    def listConnections(self, *args, **kwargs):
        destination = args[0] if args else kwargs.get("plug")
        return list(self.connections.get(destination, []))

    def isConnected(self, source, destination):
        return source in self.connections.get(destination, [])

    def disconnectAttr(self, source, destination):
        self.calls.append(("disconnectAttr", source, destination))
        self.connections[destination] = [value for value in self.connections.get(destination, []) if value != source]

    def connectAttr(self, source, destination, **kwargs):
        self.calls.append(("connectAttr", source, destination, kwargs))
        if source not in self.connections.setdefault(destination, []):
            self.connections[destination].append(source)

    def attributeQuery(self, *args, **kwargs):
        return False

    def refresh(self, **kwargs):
        self.calls.append(("refresh", kwargs))


def _assignments():
    return (
        _assignment("LeftArm", "|model|left_arm"),
        _assignment("LeftForeArm", "|model|left_forearm"),
        _assignment("RightArm", "|model|right_arm"),
        _assignment("RightForeArm", "|model|right_forearm"),
    )


def _setter(cmds):
    def apply(joint, child, target):
        parent = cmds.matrices[joint]
        current = cmds.matrices[child]
        length = ((current[12] - parent[12]) ** 2 + (current[13] - parent[13]) ** 2 + (current[14] - parent[14]) ** 2) ** 0.5
        cmds.matrices[child] = _matrix(
            parent[12] + target[0] * length,
            parent[13] + target[1] * length,
            parent[14] + target[2] * length,
        )
        return {"method": "fake-world-matrix-setter"}

    return apply


def _retrying_setter(cmds, retry_joint, failed_attempts):
    calls = {}
    base_setter = _setter(cmds)

    def apply(joint, child, target):
        calls[joint] = calls.get(joint, 0) + 1
        restore_matrices = getattr(cmds, "_stance_restore_matrices", None)
        if restore_matrices is None:
            restore_matrices = cmds._stance_restore_matrices = {}
        restore_matrices.setdefault(joint, (child, list(cmds.matrices[child])))
        result = base_setter(joint, child, target)
        if joint == retry_joint and calls[joint] <= failed_attempts:
            current = cmds.matrices[child]
            cmds.matrices[child] = _matrix(current[12], current[13] + 0.01, current[14])
        return {**result, "call": calls[joint]}

    apply.calls = calls
    return apply


class TestHumanIkStance(unittest.TestCase):
    def _transaction(self, cmds=None, report=None, *, left_direction=1.0, setter=True):
        cmds = cmds or _FakeCmds(left_direction=left_direction)
        return HumanIkStanceTransaction(
            "|model",
            _assignments(),
            ownership_report=report or {"rows": [], "counts": {}},
            cmds_module=cmds,
            world_matrix_setter=_setter(cmds) if setter else None,
        ), cmds

    def test_direction_evidence_uses_maya_world_matrix(self):
        cmds = _FakeCmds()

        self.assertEqual(joint_world_direction(cmds, "|model|left_arm", "|model|left_forearm"), (1.0, 0.0, 0.0))
        self.assertEqual(direction_evidence(cmds, "|model|right_arm", "|model|right_forearm")["absoluteElevationRadians"], 0.0)

    def test_canonical_targets_are_read_only_and_source_independent(self):
        report = canonical_stance_targets(_assignments())

        self.assertTrue(report["ready"])
        self.assertEqual(report["upAxis"], "Y")
        self.assertEqual(report["directionStrategy"], "current-world-direction-horizontal-projection")

    def test_blocker_is_rejected_before_disconnect_or_pose(self):
        tx, cmds = self._transaction(report={"rows": [{"node": "physics", "classification": "physics_blocker"}], "counts": {}})

        with self.assertRaisesRegex(RuntimeError, "ownership blocked"):
            tx.prepare()
        self.assertFalse(any(call[0] in {"disconnectAttr", "setAttr"} for call in cmds.calls))

    def test_zero_edge_target_is_valid_and_restorable(self):
        tx, cmds = self._transaction()

        tx.enter()
        self.assertTrue(tx.active)
        self.assertTrue(tx.stance_evidence["pose"]["passed"])
        self.assertTrue(all(row["attemptCount"] == 1 for row in tx.stance_evidence["pose"]["rows"]))
        self.assertTrue(all(row["attempts"][0]["passed"] for row in tx.stance_evidence["pose"]["rows"]))
        json.dumps(tx.to_dict())
        restore = tx.restore()

        self.assertFalse(tx.active)
        self.assertTrue(restore["passed"])
        self.assertTrue(tx.restore()["idempotent"])
        self.assertFalse(any(call[0] == "disconnectAttr" for call in cmds.calls))

    def test_direction_retry_converges_on_second_attempt(self):
        tx, cmds = self._transaction()
        setter = _retrying_setter(cmds, "|model|left_arm", 1)
        tx.world_matrix_setter = setter

        tx.enter()

        rows = tx.stance_evidence["pose"]["rows"]
        left = next(row for row in rows if row["hikBone"] == "LeftArm")
        right = next(row for row in rows if row["hikBone"] == "RightArm")
        self.assertEqual(left["attemptCount"], 2)
        self.assertEqual(right["attemptCount"], 1)
        self.assertFalse(left["attempts"][0]["passed"])
        self.assertTrue(left["attempts"][1]["passed"])
        self.assertEqual(left["finalApply"]["call"], 2)
        self.assertEqual(left["finalDirection"], left["direction"])
        self.assertEqual(left["finalDirectionResidual"], left["directionResidual"])
        self.assertEqual(left["tolerances"]["direction"], 1.0e-8)
        self.assertEqual(left["tolerances"]["elevation"], 1.0e-4)
        tx.restore()

    def test_direction_retry_failure_reports_slot_and_final_residual(self):
        tx, cmds = self._transaction()
        original_matrices = {joint: list(matrix) for joint, matrix in cmds.matrices.items()}
        setter = _retrying_setter(cmds, "|model|left_arm", 3)
        tx.world_matrix_setter = setter

        with self.assertRaisesRegex(
            RuntimeError,
            r"hikBone=LeftArm.*directionResidual=.*tolerance=1e-08.*elevationRadians=.*tolerance=0.0001.*attempts=3",
        ) as context:
            tx.enter()

        self.assertFalse(tx.active)
        left = next(row for row in tx.stance_evidence["pose"]["rows"] if row["hikBone"] == "LeftArm")
        self.assertEqual(left["attemptCount"], 3)
        self.assertFalse(left["passed"])
        self.assertEqual(left["attempts"][-1]["elevationRadians"], left["finalElevationRadians"])
        self.assertIn("tolerance=0.0001", str(context.exception))
        self.assertTrue(tx.stance_evidence["restore"]["passed"])
        self.assertEqual(cmds.matrices, original_matrices)

    def test_targets_use_each_arm_horizontal_projection(self):
        cmds = _FakeCmds()
        cmds.matrices["|model|left_forearm"] = _matrix(1.0, 2.0, 0.0)
        tx, _ = self._transaction(cmds=cmds)

        tx.prepare()

        self.assertEqual(tx.stance_evidence["targets"]["LeftArm"]["targetDirection"], [1.0, 0.0, 0.0])

    def test_n_edge_target_disconnects_and_reconnects_exact_edges(self):
        cmds = _FakeCmds()
        destination = "|model|left_arm.rotateX"
        cmds.connections[destination] = ["|ik_ctrl.outputRotateX"]
        report = {
            "rows": [{
                "node": "|ik_ctrl",
                "nodeType": "mmdCcdIk",
                "classification": "mute_for_hik",
                "writes": [destination],
                "reads": [],
            }],
            "counts": {"mute_for_hik": 1},
        }
        tx, cmds = self._transaction(cmds=cmds, report=report)

        tx.enter()
        self.assertEqual(cmds.connections[destination], [])
        tx.restore()

        self.assertEqual(cmds.connections[destination], ["|ik_ctrl.outputRotateX"])
        self.assertTrue(any(call[0] == "connectAttr" and call[3].get("force") is False for call in cmds.calls))

    def test_apply_failure_rolls_back_pose_and_topology(self):
        tx, cmds = self._transaction()
        calls = []

        def failing_setter(joint, child, target):
            calls.append(joint)
            if joint == "|model|right_arm":
                raise RuntimeError("locked rotate")
            return _setter(cmds)(joint, child, target)

        tx.world_matrix_setter = failing_setter
        with self.assertRaisesRegex(RuntimeError, "locked rotate"):
            tx.enter()
        self.assertFalse(tx.active)
        self.assertEqual(calls, ["|model|left_arm", "|model|right_arm"])

    def test_restore_failure_keeps_active_for_retry(self):
        cmds = _FakeCmds()
        destination = "|model|left_arm.rotateX"
        cmds.connections[destination] = ["|ik_ctrl.outputRotateX"]
        tx, cmds = self._transaction(cmds=cmds, report={
            "rows": [{
                "node": "|ik_ctrl",
                "nodeType": "mmdCcdIk",
                "classification": "mute_for_hik",
                "writes": [destination],
                "reads": [],
            }],
            "counts": {"mute_for_hik": 1},
        })
        tx.enter()
        cmds.connections[destination] = ["|third_party.output"]

        with self.assertRaisesRegex(RuntimeError, "third-party"):
            tx.restore()
        self.assertTrue(tx.active)
        cmds.connections[destination] = []
        self.assertFalse(tx.restore()["passed"] is False)

    def test_joint_orient_is_restored_and_checked(self):
        tx, cmds = self._transaction()
        tx.enter()
        cmds.attrs["|model|left_arm.jointOrient"] = [(1.0, 0.0, 0.0)]
        with self.assertRaisesRegex(RuntimeError, "residual"):
            tx.restore()
        self.assertTrue(tx.active)
        cmds.attrs["|model|left_arm.jointOrient"] = [(0.0, 0.0, 0.0)]
        tx.restore()

    def test_restore_residual_still_attempts_exact_reconnect(self):
        cmds = _FakeCmds()
        destination = "|model|left_arm.rotateX"
        cmds.connections[destination] = ["|ik_ctrl.outputRotateX"]
        tx, _ = self._transaction(
            cmds=cmds,
            report={
                "rows": [{
                    "node": "|ik_ctrl",
                    "nodeType": "mmdCcdIk",
                    "classification": "mute_for_hik",
                    "writes": [destination],
                }],
                "counts": {"mute_for_hik": 1},
            },
        )
        tx.enter()
        cmds.attrs["|model|left_arm.jointOrient"] = [(1.0, 0.0, 0.0)]

        with self.assertRaisesRegex(RuntimeError, "residual"):
            tx.restore()
        self.assertTrue(tx.active)
        self.assertEqual(cmds.connections[destination], ["|ik_ctrl.outputRotateX"])
        cmds.attrs["|model|left_arm.jointOrient"] = [(0.0, 0.0, 0.0)]
        self.assertTrue(tx.restore()["passed"])



if __name__ == "__main__":
    unittest.main()
