"""Focused tests for the read-only Maya Morph metadata repository."""

from __future__ import annotations

from unittest.mock import Mock

from mmd_tools.adapters.maya_metadata_read_support import MayaMetadataReadSupport
from mmd_tools.adapters.maya_morph_metadata_repository import (
    MayaMorphMetadataRepository,
)
from mmd_tools.adapters.scene_metadata_adapter import SceneMetadataAdapter
from tests.unit.test_maya_scene_metadata_backend import (
    FakeCmds,
    _backend,
    _morph,
    _registry,
    _snapshot_vertex_scene,
)


class _RepositoryError(ValueError):
    pass


def _repository(cmds: FakeCmds) -> MayaMorphMetadataRepository:
    support = MayaMetadataReadSupport(cmds, error_factory=_RepositoryError)
    return MayaMorphMetadataRepository(
        support,
        cmds_adapter=cmds,
        error_factory=_RepositoryError,
    )


def test_reads_selected_registry_owned_morph_without_collection_scan() -> None:
    cmds, _backend_facade = _backend()
    _morph(cmds, "morph", "group", [{"morph_index": 0, "morph_rate": 0.5}])
    _registry(cmds, morph_members=["morph"])

    morph = _repository(cmds).read_morph_value("|root", "morph", 0)

    assert morph.index == 0
    assert morph.binding_identity == "morph"
    assert morph.offsets == ({"morph_index": 0, "morph_rate": 0.5},)


def test_iterates_legacy_morphs_only_with_explicit_root_ownership() -> None:
    cmds, _backend_facade = _backend()
    _morph(cmds, "owned", "group", [], legacy_root=True)
    _morph(cmds, "other", "group", [], index=1, legacy_root=True)
    cmds.nodes.add("|otherRoot")
    cmds.connections[("other.mmd_model_root", None)] = ["|otherRoot"]

    morphs = list(_repository(cmds).iter_morph_metadata("|root"))

    assert [morph["binding_identity"] for morph in morphs] == ["owned"]


def test_snapshot_delegates_once_to_spec_reader_and_reuses_projection_queries() -> None:
    cmds, backend = _snapshot_vertex_scene()
    repository = _repository(cmds)
    backend._morph_repository = repository
    reader = Mock(wraps=lambda root: SceneMetadataAdapter(backend).read_spec(root))

    snapshot = repository.read_morph_authoring_snapshot(
        "|root",
        spec_reader=reader,
    )

    assert reader.call_count == 1
    assert snapshot.spec.morphs[0].binding_identity == "morph"
    assert snapshot.projection.binding_for_index(0).bindings[0].blend_shape_identity == "bs"
    assert snapshot.topology_inspection.valid is True
