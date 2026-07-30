"""Pure-Python contracts for the physics solver's saved bind-basis resolver."""

from __future__ import annotations

import math
import json
import sys
import unittest
from contextlib import contextmanager
from unittest.mock import Mock, patch

from tests.common.maya_stub import install_maya_stub

install_maya_stub(profile="minimal")
_om = sys.modules["maya.api.OpenMaya"]
_MISSING = object()
_OPENMAYA_PATCH_NAMES = ("MMatrix", "MPxNode", "MTypeId")


class _Matrix:
    """Small 4x4 matrix double used to exercise resolver validation."""

    def __init__(self, values):
        self.values = [float(value) for value in values]

    def __iter__(self):
        return iter(self.values)

    def getElement(self, row, column):
        return self.values[row * 4 + column]

    def inverse(self):
        values = [self.values[row * 4 : (row + 1) * 4] for row in range(4)]
        identity = [[float(row == column) for column in range(4)] for row in range(4)]
        for column in range(4):
            pivot = max(range(column, 4), key=lambda row: abs(values[row][column]))
            if abs(values[pivot][column]) <= 1e-12:
                raise ValueError("singular")
            values[column], values[pivot] = values[pivot], values[column]
            identity[column], identity[pivot] = identity[pivot], identity[column]
            scale = values[column][column]
            values[column] = [item / scale for item in values[column]]
            identity[column] = [item / scale for item in identity[column]]
            for row in range(4):
                if row == column:
                    continue
                scale = values[row][column]
                values[row] = [a - scale * b for a, b in zip(values[row], values[column])]
                identity[row] = [a - scale * b for a, b in zip(identity[row], identity[column])]
        return _Matrix([item for row in identity for item in row])


@contextmanager
def _temporary_openmaya_attrs(**attrs):
    """Install test-only OpenMaya symbols and restore the shared stub exactly."""

    previous = {name: _om.__dict__.get(name, _MISSING) for name in attrs}
    try:
        for name, value in attrs.items():
            setattr(_om, name, value)
        yield
    finally:
        for name, value in previous.items():
            if value is _MISSING:
                _om.__dict__.pop(name, None)
            else:
                setattr(_om, name, value)


_MPxNode = type("_MPxNode", (), {"__init__": lambda self: None})
with _temporary_openmaya_attrs(
    MMatrix=_Matrix,
    MPxNode=_MPxNode,
    MTypeId=lambda value: value,
):
    from mmd_tools.nodes import mmd_physics_solver_node as solver
from mmd_tools.core import physics_bind_basis as basis


IDENTITY = [
    1.0,
    0.0,
    0.0,
    0.0,
    0.0,
    1.0,
    0.0,
    0.0,
    0.0,
    0.0,
    1.0,
    0.0,
    0.0,
    0.0,
    0.0,
    1.0,
]


class _Cmds:
    def __init__(self, *, dag=None, skin=None, attrs=None):
        self.dag = dag or {}
        self.skin = skin or {}
        self.attrs = attrs or {}
        self.reads = []

    def dagPose(self, node, **kwargs):
        if kwargs.get("bindPose"):
            return self.dag.get(("poses", node), [])
        if kwargs.get("members"):
            return self.dag.get(("members", node), [])
        return []

    def getAttr(self, plug, **kwargs):
        self.reads.append(plug)
        if kwargs.get("size"):
            return self.skin.get((plug, "size"), 0)
        if plug not in self.attrs:
            raise RuntimeError(f"missing {plug}")
        return self.attrs[plug]

    def listConnections(self, plug, **kwargs):
        if kwargs.get("connections"):
            return self.skin.get((plug, "pairs"), [])
        return self.skin.get((plug, "connections"), [])

    def ls(self, node, **_kwargs):
        return [node]


class _SparsePoseCmds(_Cmds):
    """Pose double with non-contiguous members[] logical indices."""

    def getAttr(self, plug, **kwargs):
        if kwargs.get("multiIndices"):
            return [3, 7]
        return super().getAttr(plug, **kwargs)

    def listConnections(self, plug, **kwargs):
        if plug == "|ns:bindPose.members[3]":
            return ["|ns:otherJoint"]
        if plug == "|ns:bindPose.members[7]":
            return ["|ns:joint"]
        return super().listConnections(plug, **kwargs)


def _resolver_cmds(*, dag_matrix=None, skin_pre=None, joint="|ns:joint", mismatch=False):
    dag = {
        ("poses", joint): ["|ns:bindPose"],
        ("members", "|ns:bindPose"): [joint],
    }
    attrs = {}
    if dag_matrix is not None:
        attrs["|ns:bindPose.worldMatrix[0]"] = dag_matrix
    skin = {}
    if skin_pre is not None:
        skin[(f"{joint}.worldMatrix[0]", "connections")] = ["|ns:skin"]
        skin[("|ns:skin.matrix", "size")] = 1
        skin[("|ns:skin.matrix[0]", "connections")] = [f"{joint}.worldMatrix[0]"]
        attrs["|ns:skin.bindPreMatrix[0]"] = skin_pre
    return _Cmds(dag=dag if dag_matrix is not None else {}, skin=skin, attrs=attrs)


class TestPhysicsBindBasis(unittest.TestCase):
    def test_openmaya_patch_restores_exact_symbols(self):
        original = {
            name: _om.__dict__.get(name, _MISSING)
            for name in _OPENMAYA_PATCH_NAMES
        }
        with _temporary_openmaya_attrs(
            MMatrix=_Matrix,
            MPxNode=_MPxNode,
            MTypeId=lambda value: value,
        ):
            self.assertIs(_om.MMatrix, _Matrix)
            self.assertIs(_om.MPxNode, _MPxNode)
        for name, value in original.items():
            self.assertIs(_om.__dict__.get(name, _MISSING), value, name)

    def _resolve(self, cmds, joint="|ns:joint"):
        maya = sys.modules["maya"]
        old_cmds = maya.cmds
        maya.cmds = cmds
        try:
            with _temporary_openmaya_attrs(MMatrix=_Matrix):
                return basis.resolve_saved_bind_world_matrix(joint)
        finally:
            maya.cmds = old_cmds

    def test_dag_pose_is_primary_and_live_world_is_not_read(self):
        cmds = _resolver_cmds(dag_matrix=IDENTITY, skin_pre=IDENTITY)
        result = self._resolve(cmds)
        self.assertEqual(list(result), IDENTITY)
        self.assertNotIn("|ns:joint.worldMatrix[0]", cmds.reads)

    def test_skin_bind_pre_inverse_is_validated_fallback(self):
        bind_world = IDENTITY[:]
        bind_world[12] = 4.0
        pre = _Matrix(bind_world).inverse().values
        result = self._resolve(_resolver_cmds(skin_pre=pre))
        self.assertAlmostEqual(list(result)[12], 4.0)

    def test_sparse_dag_pose_logical_index_maps_to_world_matrix(self):
        cmds = _SparsePoseCmds(
            dag={
                ("poses", "|ns:joint"): ["|ns:bindPose"],
                ("members", "|ns:bindPose"): ["|ns:otherJoint", "|ns:joint"],
            },
            attrs={"|ns:bindPose.worldMatrix[7]": IDENTITY},
        )
        result = self._resolve(cmds)
        self.assertEqual(list(result), IDENTITY)

    def test_bind_failure_payload_exposes_stable_reason_and_releases_handles(self):
        node = solver.MmdPhysicsSolverNode()
        node._world = Mock()
        node._model = Mock()
        node._instance = Mock()
        with patch.object(solver.logger, "warning") as warning:
            self.assertFalse(
                node._fail_initialization(
                    model_root="|model",
                    descriptor_version=12,
                    stage="build kinematic pose data",
                    error_type="BindBasisError",
                    reason=f"{basis.BIND_BASIS_MISSING} for |model|joint: saved bind unavailable",
                    reason_code=basis.BIND_BASIS_MISSING,
                )
            )
        payload = json.loads(warning.call_args.args[1])
        self.assertIn(basis.BIND_BASIS_MISSING, payload["reason"])
        self.assertIn("|model|joint", payload["reason"])
        self.assertEqual(payload["reasonCode"], basis.BIND_BASIS_MISSING)
        self.assertIsNone(node._world)
        self.assertIsNone(node._model)
        self.assertIsNone(node._instance)

    def test_mismatch_fails_closed_with_stable_reason_code(self):
        moved = IDENTITY[:]
        moved[12] = 1.0
        with self.assertRaises(basis.BindBasisResolutionError) as context:
            self._resolve(_resolver_cmds(dag_matrix=IDENTITY, skin_pre=_Matrix(moved).inverse().values))
        self.assertEqual(context.exception.reason_code, basis.BIND_BASIS_MISMATCH)

    def test_missing_and_nonfinite_candidates_fail_closed(self):
        with self.assertRaises(basis.BindBasisResolutionError) as missing:
            self._resolve(_resolver_cmds())
        self.assertEqual(missing.exception.reason_code, basis.BIND_BASIS_MISSING)

        bad = IDENTITY[:]
        bad[0] = math.inf
        with self.assertRaises(basis.BindBasisResolutionError) as nonfinite:
            self._resolve(_resolver_cmds(dag_matrix=bad))
        self.assertEqual(nonfinite.exception.reason_code, basis.BIND_BASIS_NONFINITE)

    def test_singular_candidate_fails_closed(self):
        singular = IDENTITY[:]
        singular[0] = 0.0
        with self.assertRaises(basis.BindBasisResolutionError) as context:
            self._resolve(_resolver_cmds(dag_matrix=singular))
        self.assertEqual(context.exception.reason_code, basis.BIND_BASIS_SINGULAR)


if __name__ == "__main__":
    unittest.main()
