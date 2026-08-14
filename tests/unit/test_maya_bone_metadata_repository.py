"""Tests for the read-only Maya Bone metadata repository."""

from __future__ import annotations

from mmd_tools.adapters.maya_bone_metadata_repository import MayaBoneMetadataRepository
from mmd_tools.adapters.maya_metadata_read_support import MayaMetadataReadSupport
from mmd_tools.core.pmx_data.bone import PmxBoneFlag
from tests.unit.test_maya_scene_metadata_backend import FakeCmds, _bone


class _RepositoryError(ValueError):
    pass


def _repository(cmds: FakeCmds) -> MayaBoneMetadataRepository:
    support = MayaMetadataReadSupport(cmds, error_factory=_RepositoryError)
    return MayaBoneMetadataRepository(support, error_factory=_RepositoryError)


def test_reads_selected_bone_through_canonical_binding_without_collection_scan() -> None:
    cmds = FakeCmds()
    _bone(cmds, "|root|joint", 0)
    cmds.long_names.update({"root": "|root", "joint": "|root|joint"})

    bone = _repository(cmds).read_bone_value("root", "joint", 0)

    assert bone.binding_identity == "|root|joint"
    assert bone.index == 0
    assert bone.tail_offset == (0.0, 1.0, 0.0)


def test_iterates_full_bone_payload_and_resolves_canonical_aliases() -> None:
    cmds = FakeCmds()
    flags = (
        PmxBoneFlag.CONNECT_BONE
        | PmxBoneFlag.GRANT_PARENT_ROTATE
        | PmxBoneFlag.LOCAL
        | PmxBoneFlag.AXIS_FIXED
        | PmxBoneFlag.LOCAL_AXIS
        | PmxBoneFlag.EXTERNAL_PARENT_DEFORM
        | PmxBoneFlag.IK
    )
    _bone(cmds, "|root|joint", 0, int(flags))

    bone = list(_repository(cmds).iter_bone_metadata("|root"))[0]

    assert bone["binding_identity"] == "|root|joint"
    assert bone["connect_bone_index"] == 0
    assert bone["grant_parent_index"] == 0
    assert bone["grant_local"] is True
    assert bone["fixed_axis"] == (1.0, 0.0, 0.0)
    assert bone["local_axis_z"] == (0.0, 0.0, 1.0)
    assert bone["external_parent_key"] == 42
    assert bone["ik_target_index"] == 0
    assert bone["ik_links"] == [{"bone": 0, "limit_enabled": False}]


def test_rejects_duplicate_collection_indices_with_injected_error() -> None:
    cmds = FakeCmds()
    _bone(cmds, "|root|first", 0)
    _bone(cmds, "|root|second", 0)

    try:
        list(_repository(cmds).iter_bone_metadata("|root"))
    except _RepositoryError as exc:
        assert str(exc) == "'|root': duplicate mmd_bone_index 0"
    else:
        raise AssertionError("duplicate bone indices must fail closed")
