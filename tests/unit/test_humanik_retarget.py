"""Unit tests for non-UI HumanIK source and connection diagnostics."""

import unittest

from unittest.mock import patch

from mmd_tools.core.humanik_resolver import HumanIkBoneAssignment
from mmd_tools.core.humanik_retarget import (
    collect_humanik_incoming_writer_census,
    connect_humanik_source,
    describe_humanik_import_lock,
    diff_humanik_connections,
    find_humanik_character_for_model,
    snapshot_humanik_connections,
    verify_root_locomotion,
)


class FakeMel:
    def __init__(self):
        self.commands = []
        self.loaded = True

    def eval(self, command):
        self.commands.append(command)
        if command.startswith("exists "):
            return 1
        if command.startswith("hikGetInputType"):
            return 3
        if command.startswith("hikGetRetargetCharacterInput"):
            return "Source"
        return None


class FakeCmds:
    def __init__(self):
        self.writers = {
            "|hips.rotateY": ["mmdCcdIk1.outputRotate"],
            "|hips.translateX": ["animCurve1.output"],
        }
        self.transforms = {
            "|hips": [0.0, 10.0, 0.0],
            "|spine": [0.0, 20.0, 0.0],
            "|leftLeg": [1.0, 5.0, 0.0],
            "|targetHips": [0.0, 10.0, 0.0],
            "|targetSpine": [0.0, 20.0, 0.0],
            "|targetLeftLeg": [1.0, 5.0, 0.0],
        }

    def listConnections(self, plug, source=True, destination=False, plugs=True):
        return self.writers.get(plug, [])

    def xform(self, joint, query=False, worldSpace=False, matrix=False):
        if not matrix:
            raise AssertionError("world matrix query required")
        x, y, z = self.transforms[joint]
        return [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, x, y, z, 1.0]

    def getAttr(self, plug):
        node, attr = plug.rsplit(".", 1)
        if attr == "translate":
            return [tuple(self.transforms[node])]
        raise AssertionError(plug)

    def setAttr(self, plug, x, y, z, type="double3"):
        node, attr = plug.rsplit(".", 1)
        if attr != "translate":
            raise AssertionError(plug)
        delta = [float(x) - self.transforms[node][0], float(y) - self.transforms[node][1], float(z) - self.transforms[node][2]]
        for joint in self.transforms:
            self.transforms[joint] = [value + offset for value, offset in zip(self.transforms[joint], delta)]

    def refresh(self, force=False):
        return None


class ScalarProbeCmds:
    """Small scalar-plug double for lock/connection and restore contracts."""

    def __init__(self, *, block_hips=False, block_root=False):
        self.values = {
            "|hips": [0.0, 0.0, 0.0],
            "|root": [0.0, 0.0, 0.0],
            "|targetHips": [0.0, 0.0, 0.0],
            "|targetSpine": [0.0, 1.0, 0.0],
        }
        self.parents = {"|hips": ["|root"], "|root": []}
        self.writers = {}
        self.locked = set()
        self.writes = []
        if block_hips:
            self.writers.update(
                {
                    "|hips.translateX": ["animCurve.output"],
                    "|hips.translateY": ["animCurve.output"],
                    "|hips.translateZ": ["animCurve.output"],
                }
            )
        if block_root:
            self.locked.add("|root")

    def listRelatives(self, node, parent=False, fullPath=False):
        return self.parents.get(node, []) if parent else []

    def ls(self, node, long=False):
        aliases = {"hips": "|hips", "root": "|root"}
        return [aliases.get(node, node)]

    def lockNode(self, node, query=False, lock=False):
        return [node in self.locked]

    def listConnections(self, plug, source=True, destination=False, plugs=True):
        return self.writers.get(plug, [])

    def getAttr(self, plug, **kwargs):
        node, attr = plug.rsplit(".", 1)
        if kwargs.get("lock"):
            return attr.startswith("translate") and node in self.locked
        if kwargs.get("settable"):
            return node not in self.locked and not self.writers.get(plug)
        if attr == "translate":
            return [tuple(self.values[node])]
        if attr.startswith("translate"):
            return self.values[node]["XYZ".index(attr[-1])]
        raise AssertionError(plug)

    def setAttr(self, plug, value, *args, **kwargs):
        node, attr = plug.rsplit(".", 1)
        if attr == "translate":
            raise AssertionError("compound writes are not allowed")
        if node in self.locked or self.writers.get(plug):
            raise RuntimeError("non-writable plug")
        axis = "XYZ".index(attr[-1])
        delta = float(value) - self.values[node][axis]
        self.values[node][axis] = float(value)
        if node == "|root":
            for child in ("|hips", "|targetHips", "|targetSpine"):
                self.values[child][axis] += delta
        self.writes.append(plug)

    def xform(self, joint, query=False, worldSpace=False, matrix=False):
        x, y, z = self.values[joint]
        return [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, x, y, z, 1.0]

    def refresh(self, force=False):
        return None


class TestHumanIkRetarget(unittest.TestCase):
    def setUp(self):
        self.assignments = [
            HumanIkBoneAssignment("|hips", "下半身", "Hips", 1, "mmd_name", 1),
            HumanIkBoneAssignment("|spine", "上半身", "Spine", 8, "mmd_name", 2),
            HumanIkBoneAssignment("|leftLeg", "左足", "LeftUpLeg", 2, "mmd_name", 3),
        ]

    def test_connect_humanik_source_reports_direct_input(self):
        mel = FakeMel()

        report = connect_humanik_source("Target", "Source", mel_module=mel)

        self.assertTrue(report["retargetConnected"])
        self.assertEqual(report["inputType"], 3)
        self.assertEqual(report["inputTypeName"], "direct")
        self.assertIn('hikSetCharacterInput("Target", "Source");', mel.commands)

    def test_connect_humanik_source_rejects_missing_source_readback(self):
        class MissingSourceMel(FakeMel):
            def eval(self, command):
                if command.startswith("hikGetRetargetCharacterInput"):
                    return ""
                return super().eval(command)

        with self.assertRaisesRegex(RuntimeError, "source connection failed"):
            connect_humanik_source("Target", "Source", mel_module=MissingSourceMel())

        report = connect_humanik_source(
            "Target",
            "Source",
            mel_module=MissingSourceMel(),
            require_connected=False,
        )
        self.assertFalse(report["retargetConnected"])

    def test_writer_census_is_flat_and_deterministic(self):
        rows = collect_humanik_incoming_writer_census(self.assignments, cmds_module=FakeCmds(), channels=("translateX", "rotateY"))

        self.assertEqual(rows[0]["hikBone"], "Hips")
        self.assertEqual(rows[0]["writers"], ["animCurve1.output"])
        self.assertEqual(rows[1]["writers"], ["mmdCcdIk1.outputRotate"])

    def test_connection_diff_lists_removed_writer(self):
        changes = diff_humanik_connections(
            {"|hips.rotateY": ["mmdCcdIk1.outputRotate"]},
            {"|hips.rotateY": ["HIK.outputRotate"]},
        )

        self.assertEqual(changes[0]["disconnected"], ["mmdCcdIk1.outputRotate"])
        self.assertEqual(changes[0]["connected"], ["HIK.outputRotate"])
        self.assertTrue(changes[0]["replaced"])

    def test_root_locomotion_reports_group_motion(self):
        cmds = FakeCmds()
        report = verify_root_locomotion(
            "|hips",
            {"upperBody": ["|spine"], "legs": ["|leftLeg"]},
            translation=(2.0, 0.0, 0.0),
            cmds_module=cmds,
        )

        self.assertTrue(report["passed"])
        self.assertEqual(report["groups"]["legs"]["deltas"]["|leftLeg"], [2.0, 0.0, 0.0])
        self.assertEqual(len(report["beforeWorldMatrix"]["|hips"]), 16)

    def test_root_locomotion_can_drive_source_and_observe_target(self):
        cmds = FakeCmds()
        report = verify_root_locomotion(
            "|hips",
            {"upperBody": ["|targetSpine"], "legs": ["|targetLeftLeg"]},
            translation=(2.0, 0.0, 0.0),
            cmds_module=cmds,
            observed_root_joint="|targetHips",
        )

        self.assertTrue(report["passed"])
        self.assertEqual(report["driverJoint"], "|hips")
        self.assertEqual(report["rootJoint"], "|targetHips")

    def test_root_locomotion_allows_uniform_target_retarget_scale(self):
        class ScaledTargetCmds(FakeCmds):
            def setAttr(self, plug, x, y, z, type="double3"):
                node, attr = plug.rsplit(".", 1)
                if attr != "translate":
                    raise AssertionError(plug)
                delta = [
                    float(x) - self.transforms[node][0],
                    float(y) - self.transforms[node][1],
                    float(z) - self.transforms[node][2],
                ]
                for joint in self.transforms:
                    scale = 1.5 if joint.startswith("|target") else 1.0
                    self.transforms[joint] = [
                        value + offset * scale
                        for value, offset in zip(self.transforms[joint], delta)
                    ]

        report = verify_root_locomotion(
            "|hips",
            {"upperBody": ["|targetSpine"], "legs": ["|targetLeftLeg"]},
            translation=(2.0, 0.0, 0.0),
            cmds_module=ScaledTargetCmds(),
            observed_root_joint="|targetHips",
        )

        self.assertTrue(report["passed"])
        self.assertEqual(report["rootDelta"], [3.0, 0.0, 0.0])

    def test_root_locomotion_skips_connected_hips_and_restores_scalar_ancestor(self):
        cmds = ScalarProbeCmds(block_hips=True)
        before = dict((node, list(value)) for node, value in cmds.values.items())
        report = verify_root_locomotion(
            "|hips",
            {"upperBody": ["|targetSpine"]},
            translation=(2.0, 0.0, 0.0),
            cmds_module=cmds,
            observed_root_joint="|targetHips",
            source_model_root="|root",
        )

        self.assertTrue(report["supported"])
        self.assertEqual(report["selectedPlug"], "|root.translateX")
        self.assertTrue(report["passed"])
        self.assertEqual(cmds.writes, ["|root.translateX", "|root.translateX"])
        self.assertEqual(cmds.values, before)
        self.assertIn("incoming_writers", report["rejectedCandidates"][0]["reasons"])
        self.assertEqual(len(report["rejectedCandidates"]), 1)
        self.assertEqual(len(report["candidateDiagnostics"]), 2)
        self.assertTrue(report["candidateDiagnostics"][1]["selected"])

    def test_root_locomotion_returns_unsupported_without_writable_scalar(self):
        cmds = ScalarProbeCmds(block_hips=True, block_root=True)
        cmds.writers.update(
            {
                "|root.translateX": ["rootAnim.output"],
                "|root.translateY": ["rootAnim.output"],
                "|root.translateZ": ["rootAnim.output"],
            }
        )
        report = verify_root_locomotion(
            "|hips",
            [],
            translation=(1.0, 0.0, 0.0),
            cmds_module=cmds,
            observed_root_joint="|targetHips",
            source_model_root="|root",
        )

        self.assertFalse(report["supported"])
        self.assertEqual(report["reason"], "no_writable_locomotion_driver")
        self.assertEqual(cmds.writes, [])

    def test_root_locomotion_normalises_short_driver_and_root_paths(self):
        cmds = ScalarProbeCmds(block_hips=True)
        report = verify_root_locomotion(
            "hips",
            {"upperBody": ["|targetSpine"]},
            translation=(2.0, 0.0, 0.0),
            cmds_module=cmds,
            observed_root_joint="|targetHips",
            source_model_root="root",
        )

        self.assertTrue(report["passed"])
        self.assertEqual(report["driverJoint"], "|hips")
        self.assertEqual(report["candidates"], ["|hips", "|root"])

    def test_root_locomotion_fails_when_restore_is_silent_noop(self):
        class SilentRestoreCmds(ScalarProbeCmds):
            def setAttr(self, plug, value, *args, **kwargs):
                if plug == "|root.translateX" and self.writes:
                    return
                return super().setAttr(plug, value, *args, **kwargs)

        cmds = SilentRestoreCmds(block_hips=True)
        report = verify_root_locomotion(
            "|hips",
            {"upperBody": ["|targetSpine"]},
            translation=(2.0, 0.0, 0.0),
            cmds_module=cmds,
            observed_root_joint="|targetHips",
            source_model_root="|root",
        )

        self.assertFalse(report["passed"])
        self.assertFalse(report["restoreSucceeded"])
        self.assertFalse(report["restoreReadbackPassed"])
        self.assertIn("restore_readback_mismatch", report["restoreError"])

    def test_root_locomotion_does_not_cross_unreachable_model_boundary(self):
        cmds = ScalarProbeCmds(block_hips=True)
        report = verify_root_locomotion(
            "|hips",
            [],
            translation=(1.0, 0.0, 0.0),
            cmds_module=cmds,
            observed_root_joint="|targetHips",
            source_model_root="|not_an_ancestor",
        )

        self.assertFalse(report["supported"])
        self.assertEqual(report["reason"], "source_model_root_unreachable")
        self.assertEqual(cmds.writes, [])

    def test_snapshot_contains_all_requested_channels(self):
        snapshot = snapshot_humanik_connections(self.assignments[:1], cmds_module=FakeCmds())

        self.assertEqual(len(snapshot), 6)
        self.assertEqual(snapshot["|hips.translateX"], ["animCurve1.output"])


class FakeModelCmds:
    """Minimal scene-fact double for HumanIK model->character detection tests."""

    def __init__(self, hierarchy, connections=None, exists=None, hik_plugin_loaded=True):
        self.hierarchy = dict(hierarchy)
        self.connections = dict(connections or {})
        self.exists = set(exists if exists is not None else self.hierarchy)
        self.hik_plugin_loaded = hik_plugin_loaded
        self.list_connections_calls = 0

    def objExists(self, node):
        return node in self.exists

    def listRelatives(self, node, allDescendents=False, fullPath=False, type=None):
        return list(self.hierarchy.get(node, []))

    def ls(self, node, long=False):
        return [node]

    def pluginInfo(self, name, query=False, loaded=False):
        return self.hik_plugin_loaded if name == "mayaHIK" else False

    def listConnections(self, node, type=None):
        self.list_connections_calls += 1
        if type == "HIKCharacterNode":
            return list(self.connections.get(node, []))
        return []


class FakeGateMel:
    """Fake ``mel`` double for the import-gate's control-rig/input checks."""

    def __init__(self, has_control_rig=False, input_source="", raise_on_load=False):
        self.has_control_rig = has_control_rig
        self.input_source = input_source
        self.raise_on_load = raise_on_load

    def eval(self, command):
        if command.startswith("exists "):
            if self.raise_on_load:
                return 0
            return 1
        if command.startswith("hikHasControlRig"):
            return 1 if self.has_control_rig else 0
        if command.startswith("hikGetRetargetCharacterInput"):
            return self.input_source
        return None


class TestFindHumanIkCharacterForModel(unittest.TestCase):
    def test_returns_none_when_model_is_not_characterized(self):
        cmds = FakeModelCmds(hierarchy={"|model": ["|model|hips"]})

        self.assertIsNone(find_humanik_character_for_model("|model", cmds_module=cmds))

    def test_returns_character_connected_to_a_descendant_joint(self):
        cmds = FakeModelCmds(
            hierarchy={"|model": ["|model|hips", "|model|spine"]},
            connections={"|model|spine": ["Character1"]},
        )

        self.assertEqual(find_humanik_character_for_model("|model", cmds_module=cmds), "Character1")

    def test_returns_none_when_model_does_not_exist(self):
        cmds = FakeModelCmds(hierarchy={}, exists=set())

        self.assertIsNone(find_humanik_character_for_model("|missing", cmds_module=cmds))

    def test_returns_none_on_query_failure(self):
        class RaisingCmds(FakeModelCmds):
            def listRelatives(self, *args, **kwargs):
                raise RuntimeError("boom")

        cmds = RaisingCmds(hierarchy={"|model": []})

        self.assertIsNone(find_humanik_character_for_model("|model", cmds_module=cmds))

    def test_skips_connection_queries_when_hik_plugin_not_loaded(self):
        """Avoids Maya's 'invalid object type' noise for the common non-HIK case."""
        cmds = FakeModelCmds(
            hierarchy={"|model": ["|model|hips"]},
            connections={"|model|hips": ["Character1"]},
            hik_plugin_loaded=False,
        )

        result = find_humanik_character_for_model("|model", cmds_module=cmds)

        self.assertIsNone(result)
        self.assertEqual(cmds.list_connections_calls, 0)


class TestDescribeHumanIkImportLock(unittest.TestCase):
    def _cmds(self, connected_character="Character1"):
        return FakeModelCmds(
            hierarchy={"|model": ["|model|hips"]},
            connections={"|model|hips": [connected_character]} if connected_character else {},
        )

    def test_neutral_uncharacterized_model_is_unblocked(self):
        cmds = FakeModelCmds(hierarchy={"|model": ["|model|hips"]})

        lock = describe_humanik_import_lock("|model", cmds_module=cmds, mel_module=FakeGateMel())

        self.assertIsNone(lock.blocked)
        self.assertIsNone(lock.character)

    def test_source_like_characterized_no_input_no_control_rig_is_unblocked(self):
        cmds = self._cmds()
        mel = FakeGateMel(has_control_rig=False, input_source="")

        lock = describe_humanik_import_lock("|model", cmds_module=cmds, mel_module=mel)

        self.assertIsNone(lock.blocked)
        self.assertEqual(lock.character, "Character1")

    def test_target_preview_with_connected_input_is_blocked(self):
        cmds = self._cmds()
        mel = FakeGateMel(has_control_rig=False, input_source="SourceCharacter")

        lock = describe_humanik_import_lock("|model", cmds_module=cmds, mel_module=mel)

        self.assertEqual(lock.blocked, "target_preview")
        self.assertEqual(lock.character, "Character1")
        self.assertEqual(lock.input_source, "SourceCharacter")

    def test_control_rig_present_is_blocked_even_without_input(self):
        cmds = self._cmds()
        mel = FakeGateMel(has_control_rig=True, input_source="")

        lock = describe_humanik_import_lock("|model", cmds_module=cmds, mel_module=mel)

        self.assertEqual(lock.blocked, "control_rig")
        self.assertTrue(lock.has_control_rig)

    def test_control_rig_takes_precedence_over_input_source(self):
        cmds = self._cmds()
        mel = FakeGateMel(has_control_rig=True, input_source="SourceCharacter")

        lock = describe_humanik_import_lock("|model", cmds_module=cmds, mel_module=mel)

        self.assertEqual(lock.blocked, "control_rig")

    def test_missing_humanik_mel_is_unblocked_even_when_characterized(self):
        cmds = self._cmds()

        with patch(
            "mmd_tools.core.humanik_retarget.ensure_humanik_mel_loaded",
            side_effect=RuntimeError("HIK MEL unavailable"),
        ):
            lock = describe_humanik_import_lock("|model", cmds_module=cmds, mel_module=FakeGateMel())

        self.assertIsNone(lock.blocked)
        self.assertEqual(lock.character, "Character1")

    def test_uncharacterized_model_never_touches_mel(self):
        cmds = FakeModelCmds(hierarchy={"|model": ["|model|hips"]})

        with patch(
            "mmd_tools.core.humanik_retarget.ensure_humanik_mel_loaded"
        ) as ensure_loaded:
            lock = describe_humanik_import_lock("|model", cmds_module=cmds, mel_module=FakeGateMel())

        self.assertIsNone(lock.blocked)
        ensure_loaded.assert_not_called()


if __name__ == "__main__":
    unittest.main()
