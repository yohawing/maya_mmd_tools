"""Focused contracts for the atomic Bone Tab reset/reconcile path."""

from dataclasses import replace

import pytest

from mmd_tools.adapters.maya_model_authoring_coordinator import (
    MayaModelAuthoringCoordinator,
    MayaModelAuthoringCoordinatorError,
)
from mmd_tools.core.bone_authoring import BoneResetPlan, make_bone_reset_plan
from mmd_tools.core.model_authoring_spec import (
    MmdBoneSpec,
    MmdMaterialSpec,
    MmdModelAuthoringSpec,
    MmdModelSpec,
)
from mmd_tools.adapters.maya_bone_authoring import plan_bone_reset


def _spec() -> MmdModelAuthoringSpec:
    return MmdModelAuthoringSpec(
        model=MmdModelSpec("Model"),
        bones=(MmdBoneSpec("root", index=0, binding_identity="|root|joint"),),
        materials=(MmdMaterialSpec("Material", index=0, binding_identity="mat"),),
    )


class _Backend:
    def __init__(self, scene):
        self.scene = scene
        self.events = []
        self.snapshot = scene
        self.fail_bones = False

    def begin_write(self, _root):
        self.events.append("begin")
        self.snapshot = self.scene

    def rebase_write_bindings(self, _root, target):
        self.events.append("rebase")
        assert {b.binding_identity for b in self.scene.bones} == {b.binding_identity for b in target.bones}

    def apply_model_metadata(self, *_args):
        self.events.append("model")

    def apply_bone_metadata(self, *_args):
        self.events.append("bones")
        if self.fail_bones:
            raise RuntimeError("injected metadata failure")

    def apply_material_metadata(self, *_args):
        self.events.append("materials")

    def apply_morph_metadata(self, *_args):
        self.events.append("morphs")

    def commit_write(self, _root):
        self.events.append("commit")

    def rollback_write(self, _root):
        self.events.append("rollback")
        self.scene = self.snapshot


class _Metadata:
    def __init__(self, backend):
        self.backend = backend

    def read_spec(self, _root):
        return self.backend.scene


class _Materials:
    def create_material(self, *_args):
        raise AssertionError

    def resolve_material(self, *_args):
        return None

    def assign_material(self, *_args):
        raise AssertionError

    def delete_material(self, *_args):
        raise AssertionError


class _Cmds:
    def object_exists(self, _node):
        return True

    def ls(self, node, **_kwargs):
        return [node]

    def list_relatives(self, _node, **_kwargs):
        return []

    def xform(self, *_args, **_kwargs):
        return [0.0, 0.0, 0.0]


class _SceneAdapter:
    def __init__(self, *, animated=False, read_only=False, referenced=False, ordinary_connection=False, control_rig=False):
        self.animated = animated
        self.read_only = read_only
        self.referenced = referenced
        self.ordinary_connection = ordinary_connection
        self.attrs = {
            ("|root|joint", "mmd_bone_index"): 0,
            ("|root|joint", "mmd_bone_parent_index"): -1,
        }
        if control_rig:
            self.attrs[("|root", "mmd_control_rig_json")] = '{"schema":"mmd_tools.mmd_control_rig","state":"EDIT"}'

    def object_exists(self, _node):
        return True

    def ls(self, *nodes, **kwargs):
        if kwargs.get("type") in ("network", "animCurve"):
            return ["curve"] if kwargs.get("type") == "animCurve" and self.animated else []
        return list(nodes)

    def list_relatives(self, node, **kwargs):
        if node == "|root" and kwargs.get("type") == "joint":
            return ["|root|joint"]
        return []

    def attribute_exists(self, attr, node):
        return (node, attr) in self.attrs

    def get_attr(self, path):
        node, attr = path.rsplit(".", 1)
        return self.attrs[(node, attr)]

    def xform(self, *_args, **_kwargs):
        return [1.0, 2.0, 3.0]

    def current_time(self):
        return 24

    def list_connections(self, *_args, **_kwargs):
        if self.ordinary_connection:
            return ["ikHandle"]
        return ["curve"] if self.animated else []

    def node_type(self, node):
        return "animCurve" if node == "curve" else "ikHandle"

    def is_read_only(self, _node):
        return self.read_only

    def reference_query(self, _node, **_kwargs):
        return self.referenced


class _Bones:
    def __init__(self, backend):
        self.backend = backend
        self.events = []

    def plan_bone_reset(self, _root, current, _scale, _cmds, **_kwargs):
        target = replace(
            current,
            bones=(replace(current.bones[0], rest_position=(1.0, 2.0, -3.0)),),
        )
        return BoneResetPlan(
            current_spec=current,
            target_spec=target,
            expected_fingerprint=current.fingerprint(),
            rest_updated_indices=(0,),
        )

    def apply_bone_reset_structure(self, _root, plan, _cmds):
        self.events.append("reset")
        return plan.target_spec

    capture_rest_position = staticmethod(lambda *_args, **_kwargs: (0.0, 0.0, 0.0))
    register_existing_joint = staticmethod(lambda *_args, **_kwargs: None)
    apply_bone_reindex = staticmethod(lambda *_args, **_kwargs: None)
    unregister_existing_joint = staticmethod(lambda *_args, **_kwargs: None)


def _coordinator(backend, bones):
    return MayaModelAuthoringCoordinator(
        _Metadata(backend), backend, _Materials(), _Cmds(),
        bone_api=bones,
        model_scale_resolver=lambda _root: 1.0,
    )


def test_reset_uses_one_structural_transaction_and_one_rebase():
    backend = _Backend(_spec())
    bones = _Bones(backend)
    result = _coordinator(backend, bones).reset_bones("|root", bones.plan_bone_reset("|root", backend.scene, 1.0, _Cmds()))
    assert result.bones[0].rest_position == (1.0, 2.0, -3.0)
    assert bones.events == ["reset"]
    assert backend.events.count("begin") == 1
    assert backend.events.count("rebase") == 1
    assert backend.events[-1] == "commit"


def test_reset_rejects_stale_plan_without_opening_transaction():
    backend = _Backend(_spec())
    bones = _Bones(backend)
    coordinator = _coordinator(backend, bones)
    plan = coordinator.plan_bone_reset("|root")
    backend.scene = replace(backend.scene, model=MmdModelSpec("Changed"))
    with pytest.raises(MayaModelAuthoringCoordinatorError, match="stale"):
        coordinator.reset_bones("|root", plan)
    assert backend.events == []


def test_core_plan_blocks_referenced_removal_before_target():
    spec = MmdModelAuthoringSpec(
        model=MmdModelSpec("Model"),
        bones=(
            MmdBoneSpec("a", index=0, connect_bone_index=1, flags=8, binding_identity="a"),
            MmdBoneSpec("b", index=1, binding_identity="b"),
        ),
    )
    plan = make_bone_reset_plan(spec, (replace(spec.bones[0], rest_position=(1.0, 0.0, 0.0)),))
    assert not plan.is_valid
    assert plan.target_spec is None


def test_structural_failure_rolls_back_and_preserves_original_fingerprint():
    backend = _Backend(_spec())
    backend.fail_bones = True
    bones = _Bones(backend)
    coordinator = _coordinator(backend, bones)
    original = backend.scene.fingerprint()
    with pytest.raises(MayaModelAuthoringCoordinatorError):
        coordinator.reset_bones("|root", coordinator.plan_bone_reset("|root"))
    assert backend.events.count("rollback") == 1
    assert backend.scene.fingerprint() == original


def test_plan_adds_descendant_updates_rest_and_compacts_indices():
    current = MmdModelAuthoringSpec(
        model=MmdModelSpec("Model"),
        bones=(MmdBoneSpec("existing", index=2, binding_identity="existing"),),
    )
    plan = make_bone_reset_plan(
        current,
        (
            MmdBoneSpec("new", rest_position=(3.0, 4.0, 5.0), binding_identity="new"),
            replace(current.bones[0], rest_position=(1.0, 2.0, 3.0)),
        ),
        requested_order=("existing",),
    )
    assert plan.is_valid
    assert plan.added_bindings == ("new",)
    assert [bone.index for bone in plan.target_spec.bones] == [0, 1]
    assert plan.target_spec.bones[0].rest_position == (1.0, 2.0, 3.0)


def test_animation_warning_is_non_blocking_and_read_only_is_blocking():
    current = _spec()
    animated = plan_bone_reset("|root", current, 1.0, _SceneAdapter(animated=True))
    assert animated.is_valid
    assert animated.warnings and "current frame 24" in animated.warnings[0]
    ordinary = plan_bone_reset("|root", current, 1.0, _SceneAdapter(ordinary_connection=True))
    assert ordinary.is_valid and not ordinary.warnings
    blocked = plan_bone_reset("|root", current, 1.0, _SceneAdapter(referenced=True))
    assert not blocked.is_valid
    assert any("read-only" in item for item in blocked.blockers)


def test_owned_control_rig_metadata_is_a_non_blocking_animation_warning():
    plan = plan_bone_reset("|root", _spec(), 1.0, _SceneAdapter(control_rig=True))
    assert plan.is_valid
    assert any("Control Rig" in warning and "current frame 24" in warning for warning in plan.warnings)
