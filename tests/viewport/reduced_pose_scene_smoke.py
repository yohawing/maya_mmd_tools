"""Maya 2024 mayapy smoke for transactional reduced-pose scene authoring.

The smoke intentionally exercises the real ``MFnAnimCurve`` path: a sparse
two-key translation must evaluate through a nonlinear Hermite midpoint, and a
forced DG connection failure must undo connections and delete detached curves.
Run with ``C:\\Program Files\\Autodesk\\Maya2024\\bin\\mayapy.exe`` from the
repository root.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _plan(channel: str, target: str, *, value_end: float = 4.0):
    from mmd_tools.converters.vmd_reduced_pose_adapter import (
        ReducedPoseChannelReport,
        ScalarCurvePlan,
        ScalarKeyPlan,
    )

    curve = ScalarCurvePlan(
        "bone",
        0,
        target,
        channel,
        (
            ScalarKeyPlan(1.0, 0.0, 1.0, 1.0),
            ScalarKeyPlan(3.0, value_end, 1.0, 1.0),
        ),
        2,
        0.0,
    )
    report = ReducedPoseChannelReport(2, 2, 0.0, 0.0, 0.0, 0.0, (curve,))
    from mmd_tools.converters.vmd_reduced_pose_adapter import ReducedPoseChannelPlanOutcome

    return ReducedPoseChannelPlanOutcome(True, (curve,), report)


def _plug(om, path):
    selection = om.MSelectionList()
    selection.add(path)
    return selection.getPlug(0)


def _num_keys(curve_fn):
    value = getattr(curve_fn, "numKeys")
    return int(value() if callable(value) else value)


def _run_success(cmds, om, oma):
    from mmd_tools.converters.vmd_reduced_pose_scene import author_reduced_pose_channel_plan

    node = cmds.createNode("transform", name="reducedPoseSceneSmoke")
    target_path = f"{node}.translateX"
    result = author_reduced_pose_channel_plan(
        _plan("translateX", target_path),
        {(0, "translateX"): _plug(om, target_path)},
        {},
    )
    assert result.success, result.failure_reason
    assert len(result.created_curves) == 1
    curve_name = result.created_curves[0].curve_name
    quarter = float(cmds.getAttr(target_path, time=1.5))
    # Endpoint slopes are 1 while the secant is 2, so this must be the
    # nonlinear Hermite value (the linear interpolation would be 1.0).
    assert math.isclose(quarter, 0.8125, rel_tol=0.0, abs_tol=1e-6), quarter

    curve_selection = om.MSelectionList()
    curve_selection.add(curve_name)
    curve_fn = oma.MFnAnimCurve(curve_selection.getDependNode(0))
    assert _num_keys(curve_fn) == 2
    assert curve_fn.inTangentType(0) == oma.MFnAnimCurve.kTangentFixed
    assert curve_fn.outTangentType(0) == oma.MFnAnimCurve.kTangentFixed
    return node, curve_name


class _FailingModifier:
    """Proxy that applies the first DG batch, then raises to test rollback."""

    calls = 0

    def __init__(self):
        import maya.api.OpenMaya as om

        self._inner = om.MDGModifier()
        self._fail = self.__class__.calls == 0
        self.__class__.calls += 1

    def connect(self, source, target):
        return self._inner.connect(source, target)

    def deleteNode(self, node):
        return self._inner.deleteNode(node)

    def doIt(self):
        result = self._inner.doIt()
        if self._fail:
            raise RuntimeError("forced reduced-pose connection failure")
        return result

    def undoIt(self):
        return self._inner.undoIt()


class _OmProxy:
    MDGModifier = _FailingModifier

    def __getattr__(self, name):
        import maya.api.OpenMaya as om

        return getattr(om, name)


class _MayaApiProxy:
    om = _OmProxy()

    def __init__(self):
        import maya.api.OpenMayaAnim as oma

        self.oma = oma


def _run_rollback(cmds, om, node):
    from mmd_tools.converters.vmd_reduced_pose_scene import author_reduced_pose_channel_plan

    target_x = f"{node}.translateX"
    target_y = f"{node}.translateY"
    # A second plan is needed so the injected failure occurs after one queued
    # connection has been applied, proving that all targets are restored.
    first = _plan("translateX", target_x).curves[0]
    second = _plan("translateY", target_y).curves[0]
    from mmd_tools.converters.vmd_reduced_pose_adapter import (
        ReducedPoseChannelPlanOutcome,
        ReducedPoseChannelReport,
    )

    report = ReducedPoseChannelReport(4, 4, 0.0, 0.0, 0.0, 0.0, (first, second))
    outcome = ReducedPoseChannelPlanOutcome(True, (first, second), report)
    _FailingModifier.calls = 0
    result = author_reduced_pose_channel_plan(
        outcome,
        {(0, "translateX"): _plug(om, target_x), (0, "translateY"): _plug(om, target_y)},
        {},
        maya_api=_MayaApiProxy(),
    )
    assert not result.success
    assert result.rolled_back, result.failure_reason
    assert not cmds.listConnections(target_x, source=True, destination=False)
    assert not cmds.listConnections(target_y, source=True, destination=False)


def main() -> int:
    import maya.cmds as cmds
    import maya.standalone
    import maya.api.OpenMaya as om
    import maya.api.OpenMayaAnim as oma

    maya.standalone.initialize(name="python")
    success_node = None
    success_curve = None
    rollback_node = None
    try:
        success_node, success_curve = _run_success(cmds, om, oma)
        rollback_node = cmds.createNode("transform", name="reducedPoseSceneRollbackSmoke")
        _run_rollback(cmds, om, rollback_node)
        print("reduced_pose_scene_smoke: PASS")
        return 0
    finally:
        cleanup = [item for item in (success_curve, success_node, rollback_node) if item and cmds.objExists(item)]
        if cleanup:
            cmds.delete(cleanup)
        maya.standalone.uninitialize()


if __name__ == "__main__":
    raise SystemExit(main())
