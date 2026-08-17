"""Focused failure contracts for the payload-free physics solver."""

from __future__ import annotations

import sys
import unittest
import json
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

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
    def set_gravity(self, _gravity): return True
    def free(self): pass


class _Instance:
    def __init__(self, *, post=True, matrices=None):
        self.post, self.matrices = post, matrices or [[float(i == j) for j in range(4) for i in range(4)]]
    def set_physics_mode(self, _mode): return True
    def evaluate_rest_pose(self): return True
    def evaluate_current_pose_after_physics(self): return self.post
    def get_world_matrices(self): return self.matrices
    def free(self): pass


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

    @staticmethod
    def _warning_payloads(warning):
        return [json.loads(call.args[1]) for call in warning.call_args_list]

    def test_descriptor_exception_is_structured_and_deduplicated_until_version_change(self):
        def descriptor(_root, **_kwargs):
            raise ValueError("descriptor boom")

        modules = _runtime_modules(_World, object, object, descriptor)
        with patch.dict(sys.modules, modules), patch.object(solver.logger, "warning") as warning:
            self.assertFalse(self.node._try_initialize(descriptor_version=4))
            self.assertFalse(self.node._try_initialize(descriptor_version=4))
            self.assertFalse(self.node._try_initialize(descriptor_version=5))

        payloads = self._warning_payloads(warning)
        self.assertEqual(len(payloads), 2)
        self.assertEqual(payloads[0]["stage"], "build physics descriptors")
        self.assertEqual(payloads[0]["errorType"], "ValueError")
        self.assertEqual(payloads[0]["reason"], "descriptor boom")
        self.assertEqual(payloads[0]["modelRoot"], "|model")
        self.assertEqual(payloads[0]["descriptorVersion"], 4)
        self.assertEqual(payloads[1]["descriptorVersion"], 5)

    def test_different_failure_stage_and_reason_relog_once(self):
        phase = {"value": "physics"}

        class World(_World):
            @classmethod
            def from_descriptors(cls, *_args):
                return cls()

        def descriptor(_root, **_kwargs):
            if phase["value"] == "physics":
                raise RuntimeError("physics descriptor boom")
            return SimpleNamespace(rigid_bodies=[], joints=[], validation_errors=[])

        def model_descriptors(_root):
            raise LookupError("model descriptor boom")

        modules = _runtime_modules(World, SimpleNamespace(from_descriptors=model_descriptors), object, descriptor)
        with patch.dict(sys.modules, modules), patch.object(solver.logger, "warning") as warning:
            self.assertFalse(self.node._try_initialize(descriptor_version=8))
            phase["value"] = "model"
            self.assertFalse(self.node._try_initialize(descriptor_version=8))
            self.assertFalse(self.node._try_initialize(descriptor_version=8))

        payloads = self._warning_payloads(warning)
        self.assertEqual(len(payloads), 2)
        self.assertEqual(payloads[0]["stage"], "build physics descriptors")
        self.assertEqual(payloads[0]["reason"], "physics descriptor boom")
        self.assertEqual(payloads[1]["stage"], "create runtime model")
        self.assertEqual(payloads[1]["reason"], "model descriptor boom")

    def test_success_clears_failure_dedupe_state(self):
        phase = {"value": "failure"}

        def descriptor(_root, **_kwargs):
            if phase["value"] == "failure":
                raise RuntimeError("retryable descriptor boom")
            return SimpleNamespace(rigid_bodies=[], joints=[], validation_errors=[])

        class World(_World):
            @classmethod
            def from_descriptors(cls, *_args):
                return cls()

        model = SimpleNamespace(from_descriptors=lambda _desc: SimpleNamespace(free=lambda: None))
        instance = SimpleNamespace(for_model=lambda _model: _Instance())
        modules = _runtime_modules(World, model, instance, descriptor)
        with patch.dict(sys.modules, modules), patch.object(solver.logger, "warning") as warning:
            self.assertFalse(self.node._try_initialize(descriptor_version=9))
            phase["value"] = "success"
            self.assertTrue(self.node._try_initialize(descriptor_version=9))
            self.node._world = self.node._model = self.node._instance = None
            self.node._initialized = False
            phase["value"] = "failure"
            self.assertFalse(self.node._try_initialize(descriptor_version=9))

        payloads = self._warning_payloads(warning)
        self.assertEqual(len(payloads), 2)
        self.assertEqual(payloads[0]["reason"], "retryable descriptor boom")
        self.assertEqual(payloads[1]["reason"], "retryable descriptor boom")

    def test_validation_error_preserves_structured_detail(self):
        validation = SimpleNamespace(index=4, kind="missing-parent", field="bone[4]", message="parent index is invalid")

        def descriptor(_root, **_kwargs):
            return SimpleNamespace(rigid_bodies=[], joints=[], validation_errors=[validation])

        modules = _runtime_modules(_World, object, object, descriptor)
        with patch.dict(sys.modules, modules), patch.object(solver.logger, "warning") as warning:
            self.assertFalse(self.node._try_initialize(descriptor_version=10))

        payload = self._warning_payloads(warning)[0]
        self.assertEqual(payload["stage"], "validate physics descriptors")
        self.assertEqual(payload["errorType"], "ValidationError")
        self.assertEqual(payload["validationErrors"], [{
            "index": 4,
            "kind": "missing-parent",
            "field": "bone[4]",
            "message": "parent index is invalid",
        }])

    def test_repeated_validation_failure_deduplicates_before_details_build(self):
        validation = SimpleNamespace(index=7, kind="invalid-parent", field="parent", message="target missing")

        def descriptor(_root, **_kwargs):
            return SimpleNamespace(rigid_bodies=[], joints=[], validation_errors=[validation])

        modules = _runtime_modules(_World, object, object, descriptor)
        with patch.dict(sys.modules, modules), patch.object(solver.logger, "warning"):
            with patch.object(
                self.node,
                "_validation_error_details",
                wraps=self.node._validation_error_details,
            ) as details:
                self.assertFalse(self.node._try_initialize(descriptor_version=11))
                self.assertFalse(self.node._try_initialize(descriptor_version=11))

        details.assert_called_once()

    def test_validation_failure_latches_until_descriptor_version_changes(self):
        self.node._initialized = False
        self.node._world = self.node._instance = None
        self.node._last_descriptor_version = 11
        self.node._latched_validation_failure_descriptor_version = 11
        self.node._try_initialize = Mock(return_value=False)
        self.node._read_world_settings = Mock(return_value=(True, -1))
        data = _Data(time=0.0)
        data.inputs[solver.MmdPhysicsSolverNode.aInDescriptorVersion] = 11

        # The compute path owns the latch check.  The same invalid descriptor
        # version must not rebuild a world or repeat diagnostics.
        _compute(self.node, data)
        self.node._try_initialize.assert_not_called()

        # A changed descriptor version unlocks a fresh attempt.
        data.inputs[solver.MmdPhysicsSolverNode.aInDescriptorVersion] = 12
        _compute(self.node, data)
        self.node._try_initialize.assert_called_once_with()


def _prepare_node(*, world, instance, time=0.0, last_time=None):
    node = solver.MmdPhysicsSolverNode()
    node._initialized, node._world, node._instance = True, world, instance
    node._read_world_settings = lambda: (True, -1)
    node._read_world_gravity = lambda: (0.0, -9.8, 0.0)
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
    def test_world_off_short_circuits_initialization_and_preserves_versions(self):
        node = solver.MmdPhysicsSolverNode()
        node._read_world_settings = Mock(return_value=(False, 7))
        node._try_initialize = Mock(return_value=True)
        node._last_world_settings_version = 3
        node._last_descriptor_version = 5
        node._last_time = 12.0
        data = _Data(time=13.0)
        data.inputs[solver.MmdPhysicsSolverNode.aInWorldSettingsVersion] = 4
        data.inputs[solver.MmdPhysicsSolverNode.aInDescriptorVersion] = 6

        solved, status, matrices = _compute(node, data)

        self.assertFalse(solved)
        self.assertEqual(status, "disabled")
        self.assertEqual(matrices, [])
        node._try_initialize.assert_not_called()
        self.assertEqual(node._last_world_settings_version, 3)
        self.assertEqual(node._last_descriptor_version, 5)
        self.assertIsNone(node._last_time)

        # Re-enabling with the versions changed while OFF must still take the
        # normal invalidation path before initialization.
        node._read_world_settings.return_value = (True, 7)
        node._free_handles = Mock()
        node._try_initialize.reset_mock()
        node._try_initialize.return_value = False
        _compute(node, data)

        node._free_handles.assert_called_once_with()
        node._try_initialize.assert_called_once_with()
        self.assertEqual(node._last_world_settings_version, 4)
        self.assertEqual(node._last_descriptor_version, 6)

    def test_reset_failure_clears_cache(self):
        instance = _Instance()
        instance.set_physics_mode = lambda _mode: False
        node, data = _prepare_node(world=_World(), instance=instance)
        node._last_kinematic_pose_signature = ((0, "matrix", (1.0,)),)
        solved, status, matrices = _compute(node, data)
        self.assertFalse(solved)
        self.assertIn("failed", status)
        self.assertEqual(matrices, [])
        self.assertIsNone(node._last_kinematic_pose_signature)

    def test_step_and_post_failure_clear_cache(self):
        node, data = _prepare_node(
            world=_World(step=object()), instance=_Instance(post=False), time=1.0, last_time=0.0
        )
        solved, status, matrices = _compute(node, data)
        self.assertFalse(solved)
        self.assertIn("failed", status)
        self.assertEqual(matrices, [])


class TestSameTimePoseCache(unittest.TestCase):
    """Same-time Maya-pose pulls are cached only for an identical input pose."""

    def _prepare_pose_node(self, *, previous_signature, current_signature):
        node, data = _prepare_node(
            world=_World(), instance=_Instance(), time=0.0, last_time=0.0
        )
        data.inputs[solver.MmdPhysicsSolverNode.aInputMode] = solver.INPUT_MODE_MAYA_POSE
        node._kinematic_corrections = {0: object()}
        pose_input = ([0.0] * 16, [1], current_signature)
        node._read_kinematic_pose_inputs = Mock(return_value=pose_input)
        node._last_kinematic_pose_signature = previous_signature
        node._reset_world = Mock(return_value=True)
        node._update_cached_matrices = Mock(return_value=True)
        return node, data, pose_input

    def test_unchanged_maya_pose_is_cached_without_reset(self):
        signature = ((0, "matrix", (1.0,)),)
        node, data, _pose_input = self._prepare_pose_node(
            previous_signature=signature, current_signature=signature
        )

        solved, status, _matrices = _compute(node, data)

        self.assertTrue(solved)
        self.assertEqual(status, "cached")
        node._reset_world.assert_not_called()
        node._read_kinematic_pose_inputs.assert_called_once_with(data)

    def test_changed_maya_pose_uses_pose_updated_reset_route(self):
        previous = ((0, "matrix", (1.0,)),)
        current = ((0, "matrix", (2.0,)),)
        node, data, pose_input = self._prepare_pose_node(
            previous_signature=previous, current_signature=current
        )

        solved, status, _matrices = _compute(node, data)

        self.assertTrue(solved)
        self.assertEqual(status, "pose-updated")
        node._reset_world.assert_called_once_with(
            solver.INPUT_MODE_MAYA_POSE, data, pose_input=pose_input
        )

    def test_failed_pose_read_never_counts_as_unchanged(self):
        signature = ((0, "matrix", (1.0,)),)
        node, data, _pose_input = self._prepare_pose_node(
            previous_signature=signature, current_signature=signature
        )
        node._read_kinematic_pose_inputs.return_value = None

        solved, status, _matrices = _compute(node, data)

        self.assertTrue(solved)
        self.assertEqual(status, "pose-updated")
        node._reset_world.assert_called_once_with(
            solver.INPUT_MODE_MAYA_POSE, data, pose_input=None
        )

    def test_rest_only_same_time_cache_remains_unchanged(self):
        node, data = _prepare_node(
            world=_World(), instance=_Instance(), time=0.0, last_time=0.0
        )
        node._reset_world = Mock(return_value=True)

        solved, status, _matrices = _compute(node, data)

        self.assertTrue(solved)
        self.assertEqual(status, "cached")
        node._reset_world.assert_not_called()
