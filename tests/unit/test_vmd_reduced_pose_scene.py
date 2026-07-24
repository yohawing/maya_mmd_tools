"""Fake-Maya tests for transactional reduced-pose scene authoring."""

from __future__ import annotations

import math
import unittest

from mmd_tools.converters.vmd_reduced_pose_adapter import (
    ReducedPoseChannelPlanOutcome,
    ReducedPoseChannelReport,
    ScalarCurvePlan,
    ScalarKeyPlan,
)
from mmd_tools.converters.vmd_reduced_pose_scene import (
    author_reduced_pose_channel_plan,
)


class _FakePlug:
    def __init__(self, name, curve_type="TL", *, destination=False, unit_type=None, free=True):
        self._name = name
        self.curve_type = curve_type
        self.unit_type = unit_type
        self.isNull = False
        self.isDestination = destination
        self.free = free
        self.connected = None

    def name(self):
        return self._name

    def isFreeToChange(self):
        return self.free

    def attribute(self):
        return self.unit_type


class _FakeTime:
    def __init__(self, value, _unit):
        self.value = float(value)


class _FakeMTimeArray(list):
    pass


class _FakeDoubleArray(list):
    pass


class _FakeNode:
    def __init__(self, name, curve_type):
        self.name = name
        self.curve_type = curve_type
        self.keys = []
        self.tangents = []
        self.output = _FakePlug(f"{name}.output", curve_type)


class _FakeAnimCurveFn:
    kAnimCurveTA = "TA"
    kAnimCurveTL = "TL"
    kAnimCurveTU = "TU"
    kTangentFixed = "fixed"

    nodes = []
    fail_on_connect_name = None

    def __init__(self, node=None):
        self.node = node

    def create(self, curve_type):
        node = _FakeNode(f"reducedCurve{len(self.nodes) + 1}", curve_type)
        self.nodes.append(node)
        self.node = node
        return node

    def addKeys(self, times, values, *_args):
        self.node.keys = [(time.value, float(value)) for time, value in zip(times, values)]

    def setTangentsLocked(self, index, locked):
        self.node.tangents.append(("locked", index, locked))

    def setInTangentType(self, index, tangent_type):
        self.node.tangents.append(("in_type", index, tangent_type))

    def setOutTangentType(self, index, tangent_type):
        self.node.tangents.append(("out_type", index, tangent_type))

    def setTangent(self, index, x, slope, is_in, **kwargs):
        self.node.tangents.append(("slope", index, x, float(slope), is_in, kwargs.get("convertUnits")))

    def findPlug(self, _name, *_args):
        return self.node.output

    def name(self):
        return self.node.name


class _FakeDGModifier:
    current = None

    def __init__(self):
        self.operations = []
        self.applied = []
        _FakeDGModifier.current = self

    def connect(self, source, target):
        self.operations.append((source, target))

    def newPlugValueMAngle(self, target, value):
        self.operations.append(("angle", target, value))

    def newPlugValueMDistance(self, target, value):
        self.operations.append(("distance", target, value))

    def newPlugValueDouble(self, target, value):
        self.operations.append(("double", target, value))

    def doIt(self):
        for operation in self.operations:
            if operation[0] == "delete":
                node = operation[1]
                if node in _FakeAnimCurveFn.nodes:
                    _FakeAnimCurveFn.nodes.remove(node)
                continue
            if operation[0] in {"angle", "distance", "double"}:
                _kind, target, value = operation
                target.static_value = value
                continue
            source, target = operation
            if _FakeAnimCurveFn.fail_on_connect_name == target.name():
                raise RuntimeError("forced connect failure")
            target.connected = source
            self.applied.append((source, target))

    def undoIt(self):
        for _source, target in reversed(self.applied):
            target.connected = None
        self.applied = []

    def deleteNode(self, node):
        self.operations.append(("delete", node))


class _FakeOM:
    MTime = type("MTime", (), {"uiUnit": staticmethod(lambda: "film")})
    MTime.__init__ = _FakeTime.__init__
    MTimeArray = _FakeMTimeArray
    MDoubleArray = _FakeDoubleArray
    MDGModifier = _FakeDGModifier

    class MFnUnitAttribute:
        kAngle = "angle"
        kDistance = "distance"

        def __init__(self, attribute):
            self._attribute = attribute

        def unitType(self):
            return self._attribute

    class MAngle:
        kDegrees = "degrees"
        kRadians = "radians"

        def __init__(self, value, unit=None):
            self.value = float(value)
            self.unit = unit

    class MDistance:
        @staticmethod
        def uiUnit():
            return "ui"

        def __init__(self, value, unit=None):
            self.value = float(value)
            self.unit = unit


class _FakeOMA:
    MFnAnimCurve = _FakeAnimCurveFn


class _FakeMayaApi:
    om = _FakeOM
    oma = _FakeOMA


def _plan(*curves):
    report = ReducedPoseChannelReport(
        source_key_count=sum(curve.source_key_count for curve in curves),
        reduced_key_count=sum(len(curve.keys) for curve in curves),
        reduction_ratio=0.0,
        max_translate_error=0.0,
        max_rotate_error_radians=0.0,
        max_morph_error=0.0,
        curve_reports=tuple(curves),
    )
    return ReducedPoseChannelPlanOutcome(True, tuple(curves), report)


def _curve(owner_kind, owner_index, channel, target):
    keys = (ScalarKeyPlan(10.0, 0.0, 0.0, 0.0), ScalarKeyPlan(12.0, 1.0, 0.5, 0.5))
    return ScalarCurvePlan(owner_kind, owner_index, target, channel, keys, 2, 0.0)


class ReducedPoseSceneAuthoringTest(unittest.TestCase):
    def setUp(self):
        _FakeAnimCurveFn.nodes = []
        _FakeAnimCurveFn.fail_on_connect_name = None

    def test_success_creates_fixed_curve_and_connects_explicit_target(self):
        target = _FakePlug("joint.translateX", "TL")
        plan = _curve("bone", 0, "translateX", "joint.translateX")

        result = author_reduced_pose_channel_plan(
            _plan(plan),
            {(0, "translateX"): target},
            {},
            maya_api=_FakeMayaApi,
        )

        self.assertTrue(result.success)
        self.assertFalse(result.rolled_back)
        self.assertEqual(len(result.created_curves), 1)
        self.assertIsNotNone(target.connected)
        self.assertEqual(_FakeAnimCurveFn.nodes[0].curve_type, "TL")
        self.assertIn(("slope", 1, 1.0, 0.5, True, True), _FakeAnimCurveFn.nodes[0].tangents)

    def test_angle_tangent_slope_is_converted_from_radians_for_ui_units(self):
        target = _FakePlug("joint.rotateX", "TA")
        plan = _curve("bone", 0, "rotateX", target.name())

        result = author_reduced_pose_channel_plan(
            _plan(plan),
            {(0, "rotateX"): target},
            {},
            maya_api=_FakeMayaApi,
        )

        self.assertTrue(result.success)
        self.assertIn(
            ("slope", 1, 1.0, math.degrees(0.5), True, True),
            _FakeAnimCurveFn.nodes[0].tangents,
        )

    def test_existing_connection_is_explicit_unsupported_without_mutation(self):
        target = _FakePlug("joint.translateX", "TL", destination=True)
        plan = _curve("bone", 0, "translateX", target.name())

        result = author_reduced_pose_channel_plan(
            _plan(plan),
            {(0, "translateX"): target},
            {},
            maya_api=_FakeMayaApi,
        )

        self.assertFalse(result.success)
        self.assertFalse(result.rolled_back)
        self.assertEqual(_FakeAnimCurveFn.nodes, [])
        self.assertIsNone(target.connected)

    def test_connect_failure_deletes_every_created_curve_and_restores_targets(self):
        first = _FakePlug("joint.translateX", "TL")
        second = _FakePlug("joint.translateY", "TL")
        _FakeAnimCurveFn.fail_on_connect_name = second.name()
        plans = (
            _curve("bone", 0, "translateX", first.name()),
            _curve("bone", 0, "translateY", second.name()),
        )

        result = author_reduced_pose_channel_plan(
            _plan(*plans),
            {(0, "translateX"): first, (0, "translateY"): second},
            {},
            maya_api=_FakeMayaApi,
        )

        self.assertFalse(result.success)
        self.assertTrue(result.rolled_back)
        self.assertEqual(result.created_curves, ())
        self.assertIsNone(first.connected)
        self.assertIsNone(second.connected)
        self.assertEqual(_FakeAnimCurveFn.nodes, [])

    def test_static_angle_uses_mangle_modifier_value(self):
        curve_target = _FakePlug("joint.translateX", "TL")
        angle_target = _FakePlug("joint.rotateX", "TA", unit_type=_FakeOM.MFnUnitAttribute.kAngle)
        result = author_reduced_pose_channel_plan(
            _plan(_curve("bone", 0, "translateX", curve_target.name())),
            {(0, "translateX"): curve_target},
            {},
            maya_api=_FakeMayaApi,
            static_values={angle_target: 45.0},
        )

        self.assertTrue(result.success)
        self.assertEqual(angle_target.static_value.value, 45.0)
        self.assertEqual(angle_target.static_value.unit, _FakeOM.MAngle.kRadians)

    def test_static_distance_uses_mdistance_modifier_value(self):
        curve_target = _FakePlug("joint.translateX", "TL")
        distance_target = _FakePlug("joint.translateY", "TL", unit_type=_FakeOM.MFnUnitAttribute.kDistance)
        result = author_reduced_pose_channel_plan(
            _plan(_curve("bone", 0, "translateX", curve_target.name())),
            {(0, "translateX"): curve_target},
            {},
            maya_api=_FakeMayaApi,
            static_values={distance_target: 2.5},
        )

        self.assertTrue(result.success)
        self.assertEqual(distance_target.static_value.value, 2.5)
        self.assertEqual(distance_target.static_value.unit, _FakeOM.MDistance.uiUnit())

    def test_static_numeric_uses_double_modifier_value(self):
        curve_target = _FakePlug("joint.translateX", "TL")
        numeric_target = _FakePlug("blendShape.weight", "TU", unit_type="numeric")
        result = author_reduced_pose_channel_plan(
            _plan(_curve("bone", 0, "translateX", curve_target.name())),
            {(0, "translateX"): curve_target},
            {},
            maya_api=_FakeMayaApi,
            static_values={numeric_target: 0.25},
        )

        self.assertTrue(result.success)
        self.assertEqual(numeric_target.static_value, 0.25)

    def test_locked_static_target_is_rejected_without_authoring(self):
        curve_target = _FakePlug("joint.translateX", "TL")
        locked_target = _FakePlug("joint.translateY", "TL", free=False)
        result = author_reduced_pose_channel_plan(
            _plan(_curve("bone", 0, "translateX", curve_target.name())),
            {(0, "translateX"): curve_target},
            {},
            maya_api=_FakeMayaApi,
            static_values={locked_target: 2.5},
        )

        self.assertFalse(result.success)
        self.assertIn("locked", result.failure_reason)
        self.assertEqual(_FakeAnimCurveFn.nodes, [])

if __name__ == "__main__":
    unittest.main()
