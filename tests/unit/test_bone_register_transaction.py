"""Focused selected-joint registration transaction tests."""

from __future__ import annotations

from mmd_tools.adapters import maya_bone_authoring
from mmd_tools.adapters.maya_scene_metadata_backend import MayaSceneMetadataBackend
from mmd_tools.core.model_authoring_spec import MmdBoneSpec
from tests.unit.test_maya_scene_metadata_backend import FakeCmds, _bone, _registry


def _setup() -> tuple[FakeCmds, MayaSceneMetadataBackend, MmdBoneSpec]:
    cmds = FakeCmds()
    _registry(cmds)
    _bone(cmds, "|root|registered", 0)
    cmds.nodes.add("|root|new_joint")
    cmds.descendants.append("|root|new_joint")
    bone = MmdBoneSpec(
        "new_joint",
        name_english="new_joint",
        index=1,
        parent_index=0,
        tail_offset=(0.0, 0.0, 0.0),
        binding_identity="|root|new_joint",
    )
    return cmds, MayaSceneMetadataBackend(cmds), bone


def test_selected_registration_writes_one_joint_and_commits_without_full_read() -> None:
    cmds, backend, bone = _setup()

    backend.begin_bone_register("|root", bone)
    result = maya_bone_authoring.register_selected_joint("|root", bone, cmds)
    backend.commit_bone_register("|root", result)

    assert result == bone
    assert cmds.attrs[("|root|new_joint", "mmd_bone_index")] == 1
    assert cmds.undo_chunk_open is False
    assert backend._write_transaction is None


def test_selected_registration_failure_rolls_back_joint_preimage_and_keeps_registry() -> None:
    cmds, backend, bone = _setup()
    backend.begin_bone_register("|root", bone)
    maya_bone_authoring.register_selected_joint("|root", bone, cmds)
    cmds.attrs[("|root|new_joint", "mmd_bone_name_en")] = "mismatch"

    try:
        backend.commit_bone_register("|root", bone)
    except Exception:
        backend.rollback_write("|root")
    else:  # pragma: no cover - strict commit must detect the injected mismatch
        raise AssertionError("registration commit unexpectedly succeeded")

    assert all(node != "|root|new_joint" or attr not in {
        "mmd_bone_name",
        "mmd_bone_name_en",
        "mmd_bone_index",
        "mmd_bone_parent_index",
        "mmd_pmx_rest_position",
        "mmd_deform_layer",
        "mmd_bone_flags",
        "mmd_bone_offset",
    } for (node, attr) in cmds.attrs)


def test_unowned_or_registered_selected_joint_is_rejected_before_undo_chunk() -> None:
    cmds, backend, bone = _setup()
    foreign = MmdBoneSpec("foreign", index=1, binding_identity="|other|joint")
    try:
        backend.begin_bone_register("|root", foreign)
    except Exception as exc:
        assert "does not exist" in str(exc) or "owned by root" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("foreign joint unexpectedly accepted")
    assert cmds.undo_chunk_open is False

    cmds.attrs[("|root|new_joint", "mmd_bone_index")] = 3
    try:
        backend.begin_bone_register("|root", bone)
    except Exception as exc:
        assert "already registered" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("registered joint unexpectedly accepted")
