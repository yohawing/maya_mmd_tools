"""Unit tests for HumanIK bake routing."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from mmd_tools.core.humanik_bake import (
    _bake_route,
    _ccd_bone_slot,
    _capture_route_values,
    _resolve_foot_ik_bake_targets,
    _rollback_authoring,
    _BakeRoute,
    bake_humanik_target_preview,
)


class FakeCmds:
    def nodeType(self, node):
        return {"append": "mmdAppend", "ik": "mmdCcdIk"}[node]

    def getAttr(self, plug):
        return '{"links":[{"bone_slot":4}]}'

    def objExists(self, plug):
        return "inputRotateElement" in plug


class BakeCmds:
    def __init__(self, values=None):
        self.frame = 0
        self.values = values or {0: 0.25, 1: 0.5, 2: 0.75}
        self.keys = []
        self.stop_calls = 0
        self.curves = {"existingCurve"}
        self.deleted = []
        self.solver_enabled = True
        self.fail_on_key = None

    def currentTime(self, frame, edit=True):
        self.frame = int(frame)

    def getAttr(self, plug, **flags):
        if flags:
            if plug == "|joint.rotateX" and flags.get("lock"):
                return False
            if plug == "|joint.rotateX" and flags.get("settable"):
                return True
            if plug == "solver.enabled" and flags.get("lock"):
                return False
            if plug == "solver.enabled" and flags.get("settable"):
                return True
        if plug == "|joint.rotateX":
            return self.values[self.frame]
        if plug == "solver.enabled":
            return self.solver_enabled
        raise AssertionError(plug)

    def objExists(self, plug):
        return plug == "|joint.rotateX"

    def listConnections(self, *args, **kwargs):
        return []

    def ls(self, type=None):
        return sorted(self.curves) if type == "animCurve" else []

    def nodeType(self, node):
        return "joint"

    def attributeQuery(self, attr, node, exists=False):
        return attr == "enabled" and node == "solver"

    def setAttr(self, plug, value):
        if plug == "solver.enabled":
            self.solver_enabled = bool(value)

    def delete(self, nodes):
        self.deleted.extend(nodes)
        for node in nodes:
            self.curves.discard(node)

    def setKeyframe(self, plug, time, value):
        self.keys.append((plug, int(time), float(value)))
        self.curves.add("newCurve")
        if self.fail_on_key is not None and len(self.keys) >= self.fail_on_key:
            raise RuntimeError("key failure")


class FailingIkCmds(BakeCmds):
    def __init__(self):
        super().__init__()
        self.ik_enabled = True
        self.fail_on_key = 2

    def nodeType(self, node):
        if node == "ik":
            return "mmdCcdIk"
        return super().nodeType(node)

    def getAttr(self, plug, **flags):
        if plug == "ik.chainJson":
            return '{"links":[{"bone_slot":4}]}'
        if plug == "ik.inputRotate[4].inputRotateElementX" and flags:
            return False if flags.get("lock") else True
        if plug == "ik.inputRotate[4].inputRotateElementX":
            return 0.0
        if plug == "ik.enabled":
            if flags.get("lock") or flags.get("settable"):
                return False if flags.get("lock") else True
            return self.ik_enabled
        return super().getAttr(plug, **flags)

    def objExists(self, plug):
        return plug == "ik.inputRotate[4].inputRotateElementX" or super().objExists(plug)

    def attributeQuery(self, attr, node, exists=False):
        if attr == "enabled" and node == "ik":
            return True
        return super().attributeQuery(attr, node, exists=exists)

    def setAttr(self, plug, value):
        if plug == "ik.enabled":
            self.ik_enabled = bool(value)
            return
        super().setAttr(plug, value)

    def listConnections(self, plug, **kwargs):
        if plug == "|joint.rotateX":
            return ["ik.outputRotate[0]"]
        return []


class FailingHikCmds(BakeCmds):
    def __init__(self):
        super().__init__()
        self.hik_connected = True
        self.fail_on_key = 1

    def nodeType(self, node):
        if node == "HIKState2SK":
            return "HIKState2SK"
        return super().nodeType(node)

    def listConnections(self, plug, **kwargs):
        if plug == "|joint.rotateX" and self.hik_connected:
            return ["HIKState2SK.outputRotate"]
        return []

    def isConnected(self, source, destination):
        return self.hik_connected

    def disconnectAttr(self, source, destination):
        self.hik_connected = False

    def connectAttr(self, source, destination, force=True):
        self.hik_connected = True


class TestHumanIkBake(unittest.TestCase):
    def test_resolves_importer_leg_ik_controller_and_target(self):
        class FootIkCmds:
            def getAttr(self, plug):
                if plug == "ns:left_leg_ik_mmdCcdIk.chainJson":
                    return '{"controllerBoneSlot":7,"targetBoneSlot":9,"links":[]}'
                raise AssertionError(plug)

            def listConnections(self, plug, **_kwargs):
                return {
                    "ns:left_leg_ik_mmdCcdIk.inputTranslate[7]": ["ns:left_leg_ik.translate"],
                    "ns:left_leg_ik_mmdCcdIk.inputTranslate[9]": ["ns:left_ankle.translate"],
                    "ns:left_leg_ik_mmdCcdIk.goalWorldMatrix": ["ns:left_leg_ik.worldMatrix[0]"],
                }.get(plug, [])

            def objExists(self, node):
                return node in {"ns:left_leg_ik", "ns:left_ankle"}

        targets = _resolve_foot_ik_bake_targets(
            [
                _BakeRoute(
                    "ns:left_leg.rotateX",
                    "ns:left_leg_ik_mmdCcdIk.inputRotate[1].inputRotateElementX",
                    "mmdCcdIk",
                    node="ns:left_leg_ik_mmdCcdIk",
                )
            ],
            FootIkCmds(),
        )

        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0].controller, "ns:left_leg_ik")
        self.assertEqual(targets[0].target, "ns:left_ankle")

    def test_toe_ik_stays_on_legacy_fail_safe_route(self):
        targets = _resolve_foot_ik_bake_targets(
            [
                _BakeRoute(
                    "ns:left_ankle.rotateX",
                    "ns:left_toe_ik_mmdCcdIk.inputRotate[1].inputRotateElementX",
                    "mmdCcdIk",
                    node="ns:left_toe_ik_mmdCcdIk",
                )
            ],
            object(),
        )

        self.assertEqual(targets, ())

    def test_routes_direct_append_and_ccdik_channels(self):
        preview = SimpleNamespace(
            restore_state=SimpleNamespace(
                plugs=[
                    SimpleNamespace(plug="|appendJoint.rotate", sources=["append.outputRotate"]),
                    SimpleNamespace(plug="|ikJoint.rotate", sources=["ik.outputRotate[0]"]),
                ]
            )
        )
        cmds = FakeCmds()

        self.assertEqual(_bake_route(preview, "|direct.rotateX", cmds), "|direct.rotateX")
        self.assertEqual(
            _bake_route(preview, "|appendJoint.rotateY", cmds),
            "append.baseRotateY",
        )
        self.assertEqual(
            _bake_route(preview, "|ikJoint.rotateZ", cmds),
            "ik.inputRotate[4].inputRotateElementZ",
        )

    def test_append_route_supports_scalar_restore_state_plug(self):
        preview = SimpleNamespace(
            restore_state=SimpleNamespace(
                plugs=[
                    SimpleNamespace(plug="|appendJoint.rotateX", sources=["append.outputRotateX"]),
                ]
            )
        )

        self.assertEqual(
            _bake_route(preview, "|appendJoint.rotateX", FakeCmds()),
            "append.baseRotateX",
        )

    def test_bake_stops_preview_and_reports_keys_and_neutral_restore(self):
        preview = SimpleNamespace(
            active=True,
            restore_state=SimpleNamespace(
                plugs=[],
                nodes=[SimpleNamespace(node="solver", attributes={"enabled": True})],
            ),
        )
        cmds = BakeCmds()

        def stop(fake_preview, cmds_module=None, mel_module=None):
            fake_preview.active = False

        with patch("mmd_tools.core.humanik_bake.stop_humanik_target_preview", side_effect=stop) as stop_mock:
            result = bake_humanik_target_preview(
                preview,
                ["|joint"],
                0,
                2,
                channels=("rotateX",),
                cmds_module=cmds,
            )

        self.assertEqual(result.key_count, 3)
        self.assertTrue(result.pre_bake_restore_state_restored)
        self.assertEqual(result.disabled_ik_nodes, [])
        self.assertEqual(result.frame_errors, {0: 0.0, 1: 0.0, 2: 0.0})
        self.assertEqual(result.max_error, 0.0)
        stop_mock.assert_called_once()
        self.assertEqual([key[2] for key in cmds.keys], [0.25, 0.5, 0.75])

    def test_bake_sampling_error_still_stops_preview(self):
        preview = SimpleNamespace(active=True, restore_state=SimpleNamespace(plugs=[]))
        cmds = BakeCmds()
        cmds.getAttr = lambda plug: (_ for _ in ()).throw(RuntimeError("sample failed"))

        def stop(fake_preview, cmds_module=None, mel_module=None):
            fake_preview.active = False

        with patch("mmd_tools.core.humanik_bake.stop_humanik_target_preview", side_effect=stop) as stop_mock:
            with self.assertRaisesRegex(RuntimeError, "sample failed"):
                bake_humanik_target_preview(
                    preview,
                    ["|joint"],
                    0,
                    1,
                    channels=("rotateX",),
                    cmds_module=cmds,
                )

        stop_mock.assert_called_once()
        self.assertFalse(preview.active)

    def test_authoring_rollback_restores_scalar_routes_after_curve_delete(self):
        class CurveDeleteLeavesValueCmds:
            def __init__(self):
                self.values = {
                    "|direct.rotateX": 1.0,
                    "ik.inputRotate[4].inputRotateElementX": 2.0,
                    "append.baseRotateX": 3.0,
                }
                self.curves = {"oldCurve", "newCurve"}

            def getAttr(self, plug, **flags):
                if flags.get("lock"):
                    return False
                if flags.get("settable"):
                    return True
                return self.values[plug]

            def setAttr(self, plug, value):
                self.values[plug] = value

            def ls(self, type=None):
                return sorted(self.curves) if type == "animCurve" else []

            def delete(self, nodes):
                # Maya removes the curve but leaves the last keyed scalar value.
                self.curves.difference_update(nodes)
                self.values.update(
                    {
                        "|direct.rotateX": 10.0,
                        "ik.inputRotate[4].inputRotateElementX": 20.0,
                        "append.baseRotateX": 30.0,
                    }
                )

            def isConnected(self, source, destination):
                return True

            def connectAttr(self, source, destination, force=True):
                raise AssertionError("no writer reconnect expected")

        cmds = CurveDeleteLeavesValueCmds()
        routes = [
            _BakeRoute("|direct.rotateX", "|direct.rotateX", "direct"),
            _BakeRoute("|ccd.rotateX", "ik.inputRotate[4].inputRotateElementX", "mmdCcdIk"),
            _BakeRoute("|append.rotateX", "append.baseRotateX", "mmdAppend"),
        ]
        original = _capture_route_values(cmds, routes)
        _rollback_authoring(cmds, {"oldCurve"}, original, {}, {})

        self.assertEqual(cmds.values, original)

    def test_malformed_ccdik_chain_json_fails_closed(self):
        class Malformed(FakeCmds):
            def getAttr(self, plug):
                return '{"links":[]}'

        with self.assertRaisesRegex(RuntimeError, "out of range"):
            _ccd_bone_slot(Malformed(), "ik", 0)

    def test_authoring_failure_deletes_only_new_curves_and_restores_solver(self):
        preview = SimpleNamespace(
            active=True,
            restore_state=SimpleNamespace(
                plugs=[SimpleNamespace(plug="|joint.rotateX", sources=["ik.outputRotate[0]"])],
                nodes=[SimpleNamespace(node="ik", attributes={"enabled": True})],
            ),
        )
        cmds = FailingIkCmds()

        def stop(fake_preview, cmds_module=None, mel_module=None):
            fake_preview.active = False

        with patch("mmd_tools.core.humanik_bake.stop_humanik_target_preview", side_effect=stop):
            with self.assertRaisesRegex(RuntimeError, "key failure"):
                bake_humanik_target_preview(
                    preview,
                    ["|joint"],
                    0,
                    2,
                    channels=("rotateX",),
                    cmds_module=cmds,
                )

        self.assertTrue(cmds.ik_enabled)
        self.assertEqual(cmds.deleted, ["newCurve"])
        self.assertNotIn("existingCurve", cmds.deleted)

    def test_authoring_failure_reconnects_characterized_hik_writer(self):
        preview = SimpleNamespace(
            active=True,
            restore_state=SimpleNamespace(
                plugs=[SimpleNamespace(plug="|joint.rotateX", sources=["HIKState2SK.outputRotate"])],
                nodes=[],
            ),
        )
        cmds = FailingHikCmds()

        def stop(fake_preview, cmds_module=None, mel_module=None):
            fake_preview.active = False

        with patch("mmd_tools.core.humanik_bake.stop_humanik_target_preview", side_effect=stop):
            with self.assertRaisesRegex(RuntimeError, "key failure"):
                bake_humanik_target_preview(
                    preview,
                    ["|joint"],
                    0,
                    1,
                    channels=("rotateX",),
                    cmds_module=cmds,
                )

        self.assertTrue(cmds.hik_connected)
        self.assertEqual(cmds.deleted, ["newCurve"])


if __name__ == "__main__":
    unittest.main()
