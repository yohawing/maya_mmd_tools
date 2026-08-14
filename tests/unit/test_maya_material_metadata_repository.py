"""Focused tests for the read-only Maya Material metadata repository."""

from __future__ import annotations

from unittest.mock import Mock

from mmd_tools.adapters.maya_material_metadata_repository import (
    MayaMaterialMetadataRepository,
)
from mmd_tools.adapters.maya_metadata_read_support import MayaMetadataReadSupport
from tests.unit.test_maya_scene_metadata_backend import FakeCmds, _backend, _material


class _RepositoryError(ValueError):
    pass


def _repository(cmds: FakeCmds) -> MayaMaterialMetadataRepository:
    support = MayaMetadataReadSupport(cmds, error_factory=_RepositoryError)
    return MayaMaterialMetadataRepository(
        support,
        cmds_adapter=cmds,
        error_factory=_RepositoryError,
    )


def _material_registry(cmds: FakeCmds, *members: str) -> None:
    cmds.nodes.add("registry")
    cmds.node_types["registry"] = "network"
    cmds.attrs.update(
        {
            ("|root", "mmd_model_registry"): True,
            ("registry", "mmd_model_registry_schema"): "1",
            ("registry", "modelRoot"): True,
            ("registry", "materialMembers"): True,
        }
    )
    cmds.connections[("|root.mmd_model_registry", None)] = ["registry"]
    cmds.connections[("registry.modelRoot", None)] = ["|root"]
    cmds.connections[("registry.materialMembers", None)] = list(members)


def test_reads_selected_material_without_full_collection_read() -> None:
    cmds = FakeCmds()
    _material(cmds, "mat", 0)
    _material_registry(cmds, "mat")

    material = _repository(cmds).read_material_value("|root", "mat", 0)

    assert material.index == 0
    assert material.binding_identity == "mat"
    assert material.diffuse == (0.1, 0.2, 0.3, 0.75)


def test_reads_material_by_registry_index() -> None:
    cmds = FakeCmds()
    _material(cmds, "mat0", 0)
    _material(cmds, "mat1", 1)
    _material_registry(cmds, "mat0", "mat1")

    material = _repository(cmds).read_material_value_by_index("|root", 1)

    assert material.index == 1
    assert material.binding_identity == "mat1"


def test_iterates_legacy_material_members_with_bounded_mesh_discovery() -> None:
    cmds = FakeCmds()
    _material(cmds, "mat")
    cmds.meshes.append("|root|mesh")
    cmds.nodes.add("|root|mesh")
    cmds.node_types["|root|mesh"] = "mesh"
    cmds.node_types["sg"] = "shadingEngine"
    cmds.connections[("|root|mesh", "shadingEngine")] = ["sg"]
    cmds.connections[("sg", None)] = ["mat"]

    materials = list(_repository(cmds).iter_material_metadata("|root"))

    assert materials[0]["binding_identity"] == "mat"
    assert materials[0]["index"] == 0


def test_list_projection_does_not_call_full_material_or_texture_reads() -> None:
    cmds, _backend_facade = _backend()
    cmds.nodes.update({"registry", "matA", "matB"})
    cmds.node_types.update(
        {"registry": "network", "matA": "lambert", "matB": "lambert"}
    )
    cmds.attrs.update(
        {
            ("|root", "mmd_model_registry"): True,
            ("registry", "mmd_model_registry_schema"): "1",
            ("registry", "modelRoot"): True,
            ("registry", "materialMembers"): True,
            ("matA", "mmd_material"): 1,
            ("matA", "mmd_material_index"): 1,
            ("matA", "mmd_material_name"): "材質A",
            ("matA", "mmd_material_name_en"): "Material A",
            ("matB", "mmd_material"): 1,
            ("matB", "mmd_material_index"): 0,
            ("matB", "mmd_material_name"): "材質B",
            ("matB", "mmd_material_name_en"): "Material B",
        }
    )
    cmds.connections[("|root.mmd_model_registry", None)] = ["registry"]
    cmds.connections[("registry.modelRoot", None)] = ["|root"]
    cmds.connections[("registry.materialMembers", None)] = ["matA", "matB"]
    repository = _repository(cmds)
    repository._read_material = Mock(side_effect=AssertionError("full read forbidden"))
    repository._source_path = Mock(side_effect=AssertionError("texture read forbidden"))
    repository._resolved_path = Mock(side_effect=AssertionError("texture read forbidden"))

    projection = repository.read_material_list_projection("|root")

    assert tuple(item.index for item in projection.items) == (0, 1)
    repository._read_material.assert_not_called()
    repository._source_path.assert_not_called()
    repository._resolved_path.assert_not_called()
