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
        self.locked_plugs = set()
        self.node_types = {}
        self.raise_on_noop_write = False
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
        if kwargs.get("lock"):
            return plug in getattr(self, "locked_plugs", set())
        if plug.endswith("worldMatrix[0]"):
            return self.matrices[plug.rsplit(".worldMatrix", 1)[0]]
        return self.attrs.get(plug, 0.0)

    def setAttr(self, plug, *values, **kwargs):
        self.calls.append(("setAttr", plug, values, kwargs))
        if self.raise_on_noop_write and plug.endswith((".translate", ".rotate")):
            current = self.attrs.get(plug, 0.0)
            if isinstance(current, (tuple, list)) and len(current) == 1:
                current = current[0]
            if tuple(float(value) for value in current) == tuple(float(value) for value in values):
                raise AssertionError(f"no-op setAttr attempted for {plug}")
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
        return self.node_types.get(node, "joint")

    def ls(self, node=None, **kwargs):
        return [node] if node else []

    def listConnections(self, *args, **kwargs):
        destination = args[0] if args else kwargs.get("plug")
        values = list(self.connections.get(destination, []))
        if destination.endswith((".translate", ".rotate")):
            values.extend(
                source
                for axis in "XYZ"
                for source in self.connections.get(f"{destination}{axis}", [])
            )
        return sorted(set(values))

    def isConnected(self, source, destination):
        return source in self.connections.get(destination, [])

    def disconnectAttr(self, source, destination):
        self.calls.append(("disconnectAttr", source, destination))
        self.connections[destination] = [value for value in self.connections.get(destination, []) if value != source]

    def connectAttr(self, source, destination, **kwargs):
        self.calls.append(("connectAttr", source, destination, kwargs))
        if source not in self.connections.setdefault(destination, []):
            self.connections[destination].append(source)

    def attributeQuery(self, attribute, node=None, **kwargs):
        if kwargs.get("exists"):
            return f"{node}.{attribute}" in self.attrs
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


def _leg_assignments():
    return _assignments() + (
        _assignment("LeftUpLeg", "|model|left_leg"),
        _assignment("LeftLeg", "|model|left_knee"),
        _assignment("LeftFoot", "|model|left_ankle"),
        _assignment("LeftToeBase", "|model|left_toe"),
        _assignment("RightUpLeg", "|model|right_leg"),
        _assignment("RightLeg", "|model|right_knee"),
        _assignment("RightFoot", "|model|right_ankle"),
        _assignment("RightToeBase", "|model|right_toe"),
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


def _retrying_setter(cmds, retry_joint, failed_attempts, offset=0.01):
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
            cmds.matrices[child] = _matrix(current[12], current[13] + offset, current[14])
        if joint == retry_joint:
            # Keep the fake's local channel in sync with the posed matrix so
            # restore's residual-gated write path is exercised realistically.
            cmds.attrs[f"{joint}.rotate"] = [(0.1, 0.0, 0.0)]
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

    def test_supported_mmd_ccdik_foot_feedback_isolated_and_restored(self):
        cmds = _FakeCmds()
        node = "|left_leg_ik_mmdCcdIk"
        left_leg = "|model|left_leg.rotate"
        left_knee = "|model|left_knee.rotate"
        cmds.connections[left_leg] = [f"{node}.outputRotate[0]"]
        cmds.connections[left_knee] = [f"{node}.outputRotate[1]"]
        report = {
            "rows": [{
                "node": node,
                "nodeType": "mmdCcdIk",
                "classification": "feedback_blocker",
                "reads": ["|model|left_leg.translate", "|model|left_leg_ik.translate"],
                "readHikJoints": ["|model|left_leg"],
                "readOutsideJoints": ["|model|left_leg_ik"],
                "writes": [left_leg, left_knee],
            }],
            "counts": {"feedback_blocker": 1},
        }
        tx = HumanIkStanceTransaction(
            "|model",
            _leg_assignments(),
            ownership_report=report,
            cmds_module=cmds,
            world_matrix_setter=_setter(cmds),
        )

        tx.enter()
        self.assertEqual(cmds.connections[left_leg], [])
        self.assertEqual(cmds.connections[left_knee], [])
        self.assertEqual(
            [row["node"] for row in tx.ownership_snapshot["temporarilyIsolatedFeedbackRows"]],
            [node],
        )
        tx.restore()
        self.assertEqual(cmds.connections[left_leg], [f"{node}.outputRotate[0]"])
        self.assertEqual(cmds.connections[left_knee], [f"{node}.outputRotate[1]"])

    def test_unsupported_mmd_ccdik_feedback_remains_blocked(self):
        node = "|left_arm_ik_mmdCcdIk"
        destination = "|model|left_arm.rotate"
        tx, cmds = self._transaction(report={
            "rows": [{
                "node": node,
                "nodeType": "mmdCcdIk",
                "classification": "feedback_blocker",
                "reads": ["|model|left_arm.translate", "|model|left_arm_ik.translate"],
                "writes": [destination],
            }],
            "counts": {"feedback_blocker": 1},
        })
        cmds.connections[destination] = [f"{node}.outputRotate[0]"]

        with self.assertRaisesRegex(RuntimeError, "ownership blocked"):
            tx.prepare()
        self.assertFalse(any(call[0] == "disconnectAttr" for call in cmds.calls))

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
        setter = _retrying_setter(cmds, "|model|left_arm", 3, offset=0.2)
        tx.world_matrix_setter = setter

        with self.assertRaisesRegex(
            RuntimeError,
            r"hikBone=LeftArm.*directionResidual=.*elevationRadians=.*attempts=3",
        ) as context:
            tx.enter()

        self.assertFalse(tx.active)
        left = next(row for row in tx.stance_evidence["pose"]["rows"] if row["hikBone"] == "LeftArm")
        self.assertEqual(left["attemptCount"], 3)
        self.assertFalse(left["passed"])
        self.assertEqual(left["attempts"][-1]["elevationRadians"], left["finalElevationRadians"])
        self.assertIn("usable tolerance", str(context.exception))
        self.assertTrue(tx.stance_evidence["restore"]["passed"])
        self.assertEqual(cmds.matrices, original_matrices)

    def test_small_direction_residual_warns_and_continues(self):
        tx, cmds = self._transaction()
        setter = _retrying_setter(cmds, "|model|left_arm", 3, offset=0.01)
        tx.world_matrix_setter = setter

        with self.assertLogs("mmd_tools.core.humanik_stance", level="WARNING") as captured:
            tx.enter()

        pose = tx.stance_evidence["pose"]
        left = next(row for row in pose["rows"] if row["hikBone"] == "LeftArm")
        self.assertTrue(pose["passed"])
        self.assertFalse(pose["strictPassed"])
        self.assertTrue(pose["warning"])
        self.assertEqual(pose["warningRows"], ["LeftArm"])
        self.assertTrue(left["usablePassed"])
        self.assertFalse(left["strictPassed"])
        self.assertEqual(left["attemptCount"], 3)
        self.assertIn("continuing with a usable pose", "\n".join(captured.output))
        tx.restore()

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

    def test_direct_arm_pose_writers_are_isolated_only_during_stance(self):
        cmds = _FakeCmds()
        left_rotate = "|model|left_arm.rotate"
        left_translate = "|model|left_arm.translate"
        forearm_rotate = "|model|left_forearm.rotate"
        cmds.connections[left_rotate] = ["|left_arm_boneMorphAccum.outputRotate"]
        cmds.connections[left_translate] = ["|left_arm_boneMorphAccum.outputTranslate"]
        # Descendant animation is not part of the arm-pose writer scope.
        cmds.connections[forearm_rotate] = ["|left_forearm_anim.outputRotate"]
        tx, _ = self._transaction(cmds=cmds)
        base_setter = _setter(cmds)

        def setter(joint, child, target):
            if joint == "|model|left_arm":
                self.assertEqual(cmds.connections[left_rotate], [])
                self.assertEqual(cmds.connections[left_translate], [])
                self.assertEqual(cmds.connections[forearm_rotate], ["|left_forearm_anim.outputRotate"])
            return base_setter(joint, child, target)

        tx.world_matrix_setter = setter
        tx.enter()

        pose_edges = tx.ownership_snapshot["poseWriterEdges"]
        self.assertEqual([(edge["source"], edge["destination"]) for edge in pose_edges], [
            ("|left_arm_boneMorphAccum.outputRotate", left_rotate),
            ("|left_arm_boneMorphAccum.outputTranslate", left_translate),
        ])
        tx.restore()

        self.assertEqual(cmds.connections[left_rotate], ["|left_arm_boneMorphAccum.outputRotate"])
        self.assertEqual(cmds.connections[left_translate], ["|left_arm_boneMorphAccum.outputTranslate"])
        self.assertEqual(cmds.connections[forearm_rotate], ["|left_forearm_anim.outputRotate"])

    def test_vmd_animation_isolated_and_rest_pose_applied_before_characterize(self):
        """VMD-driven descendants enter bind/rest pose, then recover motion exactly."""
        cmds = _FakeCmds()
        joint = "|model|left_knee"
        cmds.attrs[f"{joint}.translate"] = [(0.25, 6.5, -0.5)]
        cmds.attrs[f"{joint}.rotate"] = [(24.0, 2.0, -1.0)]
        cmds.attrs[f"{joint}.jointOrient"] = [(0.0, 0.0, 0.0)]
        cmds.attrs[f"{joint}.mmd_vmd_bind_translate"] = "[0.0, 6.25, 0.0]"
        curve = "|left_knee_rotateX"
        destination = f"{joint}.rotateX"
        cmds.node_types[curve] = "animCurveTA"
        cmds.connections[destination] = [f"{curve}.output"]
        tx = HumanIkStanceTransaction(
            "|model",
            _leg_assignments(),
            ownership_report={"rows": [], "counts": {}},
            cmds_module=cmds,
            world_matrix_setter=_setter(cmds),
        )

        tx.enter()

        self.assertEqual(cmds.connections[destination], [])
        self.assertEqual(cmds.attrs[f"{joint}.translate"], [(0.0, 6.25, 0.0)])
        self.assertEqual(cmds.attrs[f"{joint}.rotate"], [(0.0, 0.0, 0.0)])
        self.assertTrue(tx.stance_evidence["restPose"]["applied"])
        self.assertEqual(
            [edge["source"] for edge in tx.ownership_snapshot["animationWriterEdges"]],
            [f"{curve}.output"],
        )

        restore = tx.restore()

        self.assertTrue(restore["passed"])
        self.assertEqual(cmds.attrs[f"{joint}.translate"], [(0.25, 6.5, -0.5)])
        self.assertEqual(cmds.attrs[f"{joint}.rotate"], [(24.0, 2.0, -1.0)])
        self.assertEqual(cmds.connections[destination], [f"{curve}.output"])

    def test_vmd_animation_layer_writer_is_also_isolated(self):
        """Maya animation-layer blend nodes are part of the VMD motion graph."""
        cmds = _FakeCmds()
        joint = "|model|left_knee"
        cmds.attrs[f"{joint}.translate"] = [(0.0, 6.5, 0.0)]
        cmds.attrs[f"{joint}.rotate"] = [(10.0, 0.0, 0.0)]
        cmds.attrs[f"{joint}.jointOrient"] = [(0.0, 0.0, 0.0)]
        cmds.attrs[f"{joint}.mmd_vmd_bind_translate"] = "[0.0, 6.25, 0.0]"
        blend = "|left_knee_rotateX_VMD_Motion"
        destination = f"{joint}.rotateX"
        cmds.node_types[blend] = "animBlendNodeAdditiveDA"
        cmds.connections[destination] = [f"{blend}.output"]
        tx = HumanIkStanceTransaction(
            "|model",
            _leg_assignments(),
            ownership_report={"rows": [], "counts": {}},
            cmds_module=cmds,
            world_matrix_setter=_setter(cmds),
        )

        tx.enter()

        self.assertEqual(cmds.connections[destination], [])
        self.assertEqual(
            tx.ownership_snapshot["animationWriterEdges"][0]["nodeType"],
            "animBlendNodeAdditiveDA",
        )
        tx.restore()
        self.assertEqual(cmds.connections[destination], [f"{blend}.output"])

    def test_animated_joint_without_vmd_rest_metadata_fails_and_reconnects(self):
        """An unknown animation graph is never treated as a valid Rest Pose."""
        cmds = _FakeCmds()
        joint = "|model|left_knee"
        cmds.attrs[f"{joint}.translate"] = [(0.0, 6.5, 0.0)]
        cmds.attrs[f"{joint}.rotate"] = [(10.0, 0.0, 0.0)]
        cmds.attrs[f"{joint}.jointOrient"] = [(0.0, 0.0, 0.0)]
        curve = "|left_knee_rotateX"
        destination = f"{joint}.rotateX"
        cmds.node_types[curve] = "animCurveTA"
        cmds.connections[destination] = [f"{curve}.output"]
        tx = HumanIkStanceTransaction(
            "|model",
            _leg_assignments(),
            ownership_report={"rows": [], "counts": {}},
            cmds_module=cmds,
            world_matrix_setter=_setter(cmds),
        )

        with self.assertRaisesRegex(RuntimeError, "no VMD bind-pose metadata"):
            tx.enter()

        self.assertFalse(tx.active)
        self.assertEqual(cmds.connections[destination], [f"{curve}.output"])

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

    def test_assignment_translate_is_restored_for_descendant_skin_products(self):
        tx, cmds = self._transaction()
        tx.enter()
        cmds.raise_on_noop_write = True
        cmds.attrs["|model|left_forearm.translate"] = [(5.0e-7, 0.0, 0.0)]

        restore = tx.restore()

        self.assertTrue(restore["passed"])
        self.assertEqual(cmds.attrs["|model|left_forearm.translate"], [(0.0, 0.0, 0.0)])

    def test_unchanged_locked_or_connected_assignments_skip_restore_writes(self):
        tx, cmds = self._transaction()
        cmds.raise_on_noop_write = True
        locked_translate = "|model|left_forearm.translate"
        connected_rotate = "|model|right_forearm.rotate"
        cmds.locked_plugs.add(locked_translate)
        cmds.connections[connected_rotate] = ["|anim.outputRotate"]

        tx.enter()
        restore = tx.restore()

        self.assertTrue(restore["passed"])
        written = {call[1] for call in cmds.calls if call[0] == "setAttr"}
        self.assertNotIn(locked_translate, written)
        self.assertNotIn(connected_rotate, written)

    def test_changed_connected_assignment_reports_context_without_writing(self):
        tx, cmds = self._transaction()
        plug = "|model|left_forearm.translate"
        cmds.connections[plug] = ["|anim.outputTranslate"]

        tx.enter()
        cmds.attrs[plug] = [(0.25, 0.0, 0.0)]

        with self.assertRaisesRegex(RuntimeError, r"left_forearm\.translate.*incoming"):
            tx.restore()
        self.assertTrue(tx.active)
        self.assertFalse(any(call[0] == "setAttr" and call[1] == plug for call in cmds.calls))

    def test_changed_locked_assignment_reports_context_without_writing(self):
        tx, cmds = self._transaction()
        plug = "|model|left_forearm.translate"
        cmds.locked_plugs.add(plug)

        tx.enter()
        cmds.attrs[plug] = [(0.25, 0.0, 0.0)]

        with self.assertRaisesRegex(RuntimeError, r"left_forearm\.translate.*locked"):
            tx.restore()
        self.assertTrue(tx.active)
        self.assertFalse(any(call[0] == "setAttr" and call[1] == plug for call in cmds.calls))

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

    def test_locked_attribute_failure_still_reconnects_and_restores_others(self):
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
        locked_plug = "|model|left_forearm.translate"
        cmds.locked_plugs.add(locked_plug)
        cmds.attrs[locked_plug] = [(0.25, 0.0, 0.0)]
        other_plug = "|model|right_forearm.translate"
        cmds.attrs[other_plug] = [(0.1, 0.0, 0.0)]

        with self.assertRaisesRegex(RuntimeError, r"left_forearm\.translate.*locked"):
            tx.restore()

        # The other joint's attribute is still restored even though the
        # locked plug's restore failed.
        self.assertEqual(cmds.attrs[other_plug], [(0.0, 0.0, 0.0)])
        # The captured mute_for_hik writer edge is reconnected even though
        # the attribute restore aggregated a failure.
        self.assertEqual(cmds.connections[destination], ["|ik_ctrl.outputRotateX"])
        self.assertTrue(tx.active)
        failures = tx.stance_evidence["restore"]["attributeFailures"]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["plug"], locked_plug)
        self.assertIn("locked", failures[0]["error"])

        cmds.locked_plugs.discard(locked_plug)
        cmds.attrs[locked_plug] = [(0.0, 0.0, 0.0)]
        restore = tx.restore()
        self.assertTrue(restore["passed"])
        self.assertFalse(tx.active)

    def test_residual_verification_failure_still_reconnects_topology(self):
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

        original_get_attr = cmds.getAttr

        def failing_get_attr(plug, **kwargs):
            if plug == "|model|left_arm.jointOrient":
                raise RuntimeError("simulated read failure")
            return original_get_attr(plug, **kwargs)

        cmds.getAttr = failing_get_attr

        # The edge should still be disconnected (isolated) before restore
        # attempts to read back attributes.
        self.assertEqual(cmds.connections[destination], [])

        with self.assertRaisesRegex(RuntimeError, "simulated read failure"):
            tx.restore()

        self.assertTrue(tx.active)
        self.assertEqual(cmds.connections[destination], ["|ik_ctrl.outputRotateX"])
        self.assertTrue(tx.stance_evidence["restore"]["topologyRestored"])
        self.assertEqual(tx.stance_evidence["restore"]["error"], "simulated read failure")

        cmds.getAttr = original_get_attr


if __name__ == "__main__":
    unittest.main()
