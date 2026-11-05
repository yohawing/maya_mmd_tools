"""Public skeleton reads compose existing model and bone readers without writes."""

import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from mmd_tools.services.scene_model_service import SceneModelService
from mmd_tools.adapters.maya_scene_metadata_backend import MayaSceneMetadataError
from tests.unit.test_maya_scene_metadata_backend import FakeCmds, _bone


class _Scene(FakeCmds):
    root = "|group|character:model"

    def __init__(self):
        super().__init__()
        self.nodes = {self.root, "|ordinary"}
        self.attrs[(self.root, "mmd_model_name")] = "モデル"
        self.matches = {"character:model": [self.root], "ambiguous": [self.root, "|other"]}

    def ls(self, query, **kwargs):
        if kwargs.get("objectsOnly"):
            attribute = query.split(".", 1)[1]
            return [node for node, attr in self.attrs if attr == attribute]
        return self.matches.get(query, [query] if query in self.nodes else [])

    def list_relatives(self, node, **kwargs):
        assert node == self.root
        assert kwargs.get("type") == "joint"
        return list(self.descendants)


@pytest.fixture
def scene():
    return _Scene()


@pytest.fixture
def bind_resolver(monkeypatch):
    resolver = Mock(return_value=tuple(float(i) for i in range(16)))
    monkeypatch.setitem(sys.modules, "mmd_tools.core.physics_bind_basis", SimpleNamespace(
        resolve_imported_bind_world_matrix=resolver,
    ))
    return resolver


def test_namespaced_snapshot_preserves_pmx_identity_and_saved_bind(scene, bind_resolver):
    child = scene.root + "|character:child"
    parent = scene.root + "|character:parent"
    _bone(scene, child, 1)
    _bone(scene, parent, 0)
    scene.attrs[(parent, "mmd_bone_name")] = "センター"

    service = SceneModelService(cmds_adapter=scene)
    assert service.list_mmd_models() == [scene.root]
    result = service.get_model_skeleton("character:model")

    assert result["model_root"] == scene.root
    assert [bone["index"] for bone in result["bones"]] == [0, 1]
    assert result["bones"][0] == {
        "index": 0, "name": "センター", "name_english": "Bone0",
        "parent_index": -1, "joint": parent,
        "pmx_rest_position": [0.0, 0.0, 0.0],
        "bind_world_matrix": list(bind_resolver.return_value),
    }
    assert result["bones"][1]["parent_index"] == 0
    assert result["bones"][1]["pmx_rest_position"] == [1.0, 0.0, 0.0]
    assert [call.args[0] for call in bind_resolver.call_args_list] == [parent, child]
    assert json.loads(json.dumps(result)) == result
    assert scene.write_history == []


@pytest.mark.parametrize("root", [None, "", "missing", "ambiguous", "|ordinary", "|deleted"])
def test_rejects_missing_ambiguous_or_non_model_roots(scene, bind_resolver, root):
    with pytest.raises(ValueError):
        SceneModelService(cmds_adapter=scene).get_model_skeleton(root)
    bind_resolver.assert_not_called()
    assert scene.write_history == []


def test_duplicate_bone_indices_keep_existing_metadata_error(scene, bind_resolver):
    _bone(scene, scene.root + "|first", 0)
    _bone(scene, scene.root + "|second", 0)
    with pytest.raises(MayaSceneMetadataError, match="duplicate mmd_bone_index"):
        SceneModelService(cmds_adapter=scene).get_model_skeleton(scene.root)
    bind_resolver.assert_not_called()


def test_bind_failure_is_propagated_without_pose_fallback(scene, bind_resolver):
    _bone(scene, scene.root + "|bone", 0)
    error = RuntimeError("bind_basis_missing")
    bind_resolver.side_effect = error
    with pytest.raises(RuntimeError) as caught:
        SceneModelService(cmds_adapter=scene).get_model_skeleton(scene.root)
    assert caught.value is error
    assert scene.write_history == []
