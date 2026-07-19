"""Focused failure contracts for the payload-free physics solver."""

from __future__ import annotations

import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from tests.common.maya_stub import install_maya_stub

install_maya_stub(profile="minimal")
_om = sys.modules["maya.api.OpenMaya"]
class _MPxNode:
    def __init__(self): pass
class _MTypeId:
    def __init__(self, value): self.value = value
class _DoubleArrayData:
    def create(self, values): return list(values)
_om.MPxNode = _MPxNode
_om.MTypeId = _MTypeId
_om.MFnDoubleArrayData = _DoubleArrayData
_om.MDoubleArray = list

from mmd_tools.nodes import mmd_physics_solver_node as solver
for _name, _value in {
    "aEnable": "enable", "aInputMode": "inputMode", "aInTime": "time",
    "aInWorldSettingsVersion": "worldVersion", "aInDescriptorVersion": "descriptorVersion",
    "aOutBoneMatrices": "matrices", "aOutBoneCount": "count", "aOutStatus": "status",
    "aOutSolved": "solved",
}.items():
    setattr(solver.MmdPhysicsSolverNode, _name, _value)
class _MData:
    def __init__(self, value=None): self.value = value
    def set(self, value): self.value = value
    setBool = setString = setInt = setMObject = set
class _Input:
    def __init__(self, value): self.value = value
    def asBool(self): return bool(self.value)
    def asShort(self): return int(self.value)
    def asInt(self): return int(self.value)
    def asTime(self): return SimpleNamespace(asUnits=lambda _unit: self.value)
class _Data:
    def __init__(self, *, time=0.0):
        S = solver.MmdPhysicsSolverNode
        self.inputs = {S.aEnable: True, S.aInputMode: solver.INPUT_MODE_REST,
                       S.aInTime: time, S.aInWorldSettingsVersion: 0,
                       S.aInDescriptorVersion: 0}
        self.outputs = {key: _MData() for key in (S.aOutBoneMatrices, S.aOutBoneCount,
                                                   S.aOutStatus, S.aOutSolved)}

    def inputValue(self, attr): return _Input(self.inputs[attr])
    def outputValue(self, attr): return self.outputs[attr]
    def setClean(self, _attr): pass


class _World:
    def __init__(self, reset=1, step=object()):
        self.reset_result, self.step_result = reset, step
    def reset(self, _instance): return self.reset_result
    def step_runtime(self, _instance, _dt): return self.step_result


class _Instance:
    def __init__(self, *, post=True, matrices=None):
        self.post, self.matrices = post, matrices or [[float(i == j) for j in range(4) for i in range(4)]]
    def set_physics_mode(self, _mode): return True
    def evaluate_rest_pose(self): return True
    def evaluate_current_pose_after_physics(self): return self.post
    def get_world_matrices(self): return self.matrices


def _module(name, **attrs):
    module = ModuleType(name)
    module.__dict__.update(attrs)
    return module


def _runtime_modules(world_cls, model_cls, instance_cls, descriptor):
    return {"mmd_tools.core.physics_solver": _module(
                "mmd_tools.core.physics_solver", _collect_bone_joints=lambda _root: []),
            "mmd_tools.core.model_dag_descriptor": _module(
                "mmd_tools.core.model_dag_descriptor",
                build_model_descriptors_from_dag=lambda _root: SimpleNamespace(bones=[object()])),
            "mmd_tools.core.physics_dag_descriptor": _module(
                "mmd_tools.core.physics_dag_descriptor", build_descriptors_from_dag=descriptor),
            "mmd_tools.core.native.mmd_anim_runtime_handles": _module(
                "mmd_tools.core.native.mmd_anim_runtime_handles",
                MmdRuntimePhysicsWorld=world_cls, MmdRuntimeModel=model_cls,
                MmdRuntimeInstance=instance_cls)}


class TestInitializationFailures(unittest.TestCase):
    def setUp(self):
        self.node = solver.MmdPhysicsSolverNode()
        self.node._get_connected_model_root = lambda: "|model"
        self.node._build_kinematic_pose_data = lambda _root: None
        self.node._build_rigid_body_shape_mapping = lambda _root: None

    def test_descriptor_exception_and_factory_failure_are_retryable(self):
        class World(_World):
            calls = 0
            @classmethod
            def from_descriptors(cls, *_args):
                cls.calls += 1
                return None if cls.calls == 1 else cls()

        Model = SimpleNamespace(from_descriptors=lambda _args: object())
        Instance = SimpleNamespace(for_model=lambda _model: object())

        calls = 0

        def descriptor(_root, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("descriptor failure")
            return SimpleNamespace(rigid_bodies=[], joints=[], validation_errors=[])

        modules = _runtime_modules(World, Model, Instance, descriptor)
        with patch.dict(sys.modules, modules):
            self.assertFalse(self.node._try_initialize())
            self.assertFalse(self.node._initialized)
            self.assertFalse(self.node._try_initialize())
            self.assertFalse(self.node._initialized)
            self.assertTrue(self.node._try_initialize())
            self.assertTrue(self.node._initialized)

    def test_any_descriptor_error_rejects_initialization(self):
        class World(_World):
            calls = 0
            @classmethod
            def from_descriptors(cls, *_args):
                cls.calls += 1
                return cls()

        def invalid(_root, **_kwargs):
            return SimpleNamespace(
                rigid_bodies=[], joints=[], validation_errors=[SimpleNamespace(field="other")]
            )

        modules = _runtime_modules(World, object, object, invalid)
        with patch.dict(sys.modules, modules):
            self.assertFalse(self.node._try_initialize())
        self.assertEqual(World.calls, 0)


def _prepare_node(*, world, instance, time=0.0, last_time=None):
    node = solver.MmdPhysicsSolverNode()
    node._initialized, node._world, node._instance = True, world, instance
    node._read_world_settings = lambda: (True, -1)
    node._update_rigid_body_visual_cache = lambda: None
    node._last_time, node._cached_flat = last_time, [42.0]
    return node, _Data(time=time)


def _compute(node, data):
    node.compute(SimpleNamespace(attribute=lambda: solver.MmdPhysicsSolverNode.aOutBoneMatrices), data)
    outputs = data.outputs
    return (
        outputs[solver.MmdPhysicsSolverNode.aOutSolved].value,
        outputs[solver.MmdPhysicsSolverNode.aOutStatus].value,
        outputs[solver.MmdPhysicsSolverNode.aOutBoneMatrices].value,
    )


class TestEvaluationFailures(unittest.TestCase):
    def test_reset_failure_clears_cache(self):
        instance = _Instance()
        instance.set_physics_mode = lambda _mode: False
        node, data = _prepare_node(world=_World(), instance=instance)
        solved, status, matrices = _compute(node, data)
        self.assertFalse(solved)
        self.assertIn("failed", status)
        self.assertEqual(matrices, [])

    def test_step_and_post_failure_clear_cache(self):
        node, data = _prepare_node(
            world=_World(step=object()), instance=_Instance(post=False), time=1.0, last_time=0.0
        )
        solved, status, matrices = _compute(node, data)
        self.assertFalse(solved)
        self.assertIn("failed", status)
        self.assertEqual(matrices, [])
