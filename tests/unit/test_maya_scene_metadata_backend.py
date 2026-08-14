"""Tests for the injected-Maya normalized authoring metadata backend."""

from __future__ import annotations

import json
import math
import re
from copy import deepcopy
from dataclasses import replace
from typing import Any
from unittest.mock import patch

import pytest

from mmd_tools.adapters.maya_scene_metadata_backend import MayaSceneMetadataBackend, MayaSceneMetadataError
from mmd_tools.adapters.scene_metadata_adapter import SceneMetadataAdapter, SceneMetadataError
from mmd_tools.core.model_authoring_spec import MmdMorphSpec
from mmd_tools.core.maya_name_utils import sanitize_text
from mmd_tools.core.pmx_data.bone import PmxBoneFlag


class FakeCmds:
    def __init__(self) -> None:
        self.nodes = {"|root"}
        self.descendants: list[str] = []
        self.meshes: list[str] = []
        self.attrs: dict[tuple[str, str], Any] = {}
        self.locks: dict[tuple[str, str], bool] = {}
        self.node_types: dict[str, str] = {"|root": "transform"}
        self.connections: dict[tuple[str, str | None], list[str]] = {}
        self.history: dict[str, list[str]] = {}
        self.parents: dict[str, list[str]] = {}
        self.aliases: dict[str, list[str]] = {}
        self.long_names: dict[str, str] = {}
        self.undo_enabled = True
        self.undo_snapshot: dict[tuple[str, str], Any] | None = None
        self.undo_lock_snapshot: dict[tuple[str, str], bool] | None = None
        self.undo_chunk_open = False
        self.write_history: list[str] = []
        self.fail_set_path: str | None = None
        self.ignore_set_path: str | None = None
        self.undo_count = 0

    def object_exists(self, node: str) -> bool:
        return node in self.nodes

    def list_relatives(self, node: str, **kwargs: Any) -> list[str]:
        if kwargs.get("parent"):
            return list(self.parents.get(node, []))
        assert node == "|root"
        assert kwargs.get("allDescendents") is True
        node_type = kwargs.get("type")
        if node_type == "joint":
            return self.descendants
        if node_type == "mesh":
            return self.meshes
        return []

    def attribute_exists(self, attr: str, node: str) -> bool:
        return (node, attr) in self.attrs

    def get_attr(self, path: str, **kwargs: Any) -> Any:
        node, attr = path.rsplit(".", 1)
        if kwargs.get("lock"):
            return self.locks.get((node, attr), False)
        if kwargs.get("multiIndices"):
            return self.attrs.get((node, attr), [])
        return self.attrs[(node, attr)]

    def poly_evaluate(self, node: str, **kwargs: Any) -> int:
        assert kwargs.get("vertex") is True
        return int(self.attrs[(node, "vertexCount")])

    def blend_shape(self, node: str, **kwargs: Any) -> list[Any]:
        assert kwargs.get("query") is True
        if kwargs.get("geometry"):
            return list(self.attrs[(node, "geometry")])
        if kwargs.get("geometryIndices"):
            return list(self.attrs[(node, "geometryIndices")])
        raise AssertionError(f"unexpected blend_shape query: {kwargs!r}")

    def alias_attr(self, node: str, **kwargs: Any) -> list[str]:
        assert kwargs.get("query") is True
        return list(self.aliases.get(node, []))

    def list_connections(self, query: Any, **kwargs: Any) -> list[str]:
        node_type = kwargs.get("type")
        if isinstance(query, (list, tuple)):
            result: list[str] = []
            for item in query:
                result.extend(self.list_connections(item, **kwargs))
            return list(dict.fromkeys(result))
        return list(self.connections.get((query, node_type), self.connections.get((query, None), [])))

    def list_history(self, node: str) -> list[str]:
        return list(self.history.get(node, []))

    def node_type(self, node: str) -> str:
        return self.node_types.get(node, "")

    def set_attr(self, path: str, *values: Any, **kwargs: Any) -> None:
        self.write_history.append(path)
        if path == self.fail_set_path:
            raise RuntimeError(f"injected set failure: {path}")
        if path == self.ignore_set_path:
            return
        node, attr = path.rsplit(".", 1)
        if "lock" in kwargs:
            self.locks[(node, attr)] = bool(kwargs["lock"])
        if not values and "lock" in kwargs:
            return
        if kwargs.get("type") == "double3":
            self.attrs[(node, attr)] = [tuple(values)]
        else:
            self.attrs[(node, attr)] = values[0]

    def add_attr(self, node: str, **kwargs: Any) -> None:
        attr = kwargs["longName"]
        self.attrs[(node, attr)] = "" if kwargs.get("dataType") == "string" else 0

    def delete_attr(self, path: str) -> None:
        node, attr = path.rsplit(".", 1)
        self.attrs.pop((node, attr), None)

    def undo_info(self, **kwargs: Any) -> Any:
        if kwargs.get("query") and kwargs.get("state"):
            return self.undo_enabled
        if kwargs.get("openChunk"):
            if self.undo_chunk_open:
                raise RuntimeError("chunk already open")
            self.undo_snapshot = deepcopy(self.attrs)
            self.undo_lock_snapshot = deepcopy(self.locks)
            self.undo_chunk_open = True
            return None
        if kwargs.get("closeChunk"):
            if not self.undo_chunk_open:
                raise RuntimeError("chunk is not open")
            self.undo_chunk_open = False
            return None
        raise AssertionError(f"unexpected undo_info call: {kwargs!r}")

    def undo(self) -> None:
        self.undo_count += 1
        if self.undo_chunk_open:
            raise RuntimeError("cannot undo an open chunk")
        if self.undo_snapshot is None:
            raise RuntimeError("nothing to undo")
        self.attrs = self.undo_snapshot
        self.locks = self.undo_lock_snapshot or {}
        self.undo_snapshot = None

    def ls(self, *nodes: str, **kwargs: Any) -> list[str]:
        if kwargs.get("type"):
            return [node for node, node_type in self.node_types.items() if node_type == kwargs["type"]]
        values = [
            item
            for node in nodes
            for item in (node if isinstance(node, (list, tuple)) else (node,))
            if isinstance(item, str)
        ]
        if kwargs.get("flatten"):
            flattened: list[str] = []
            for value in values:
                match = re.fullmatch(r"(.*\.vtx\[)(\d+):(\d+)(\])", value)
                if match is None:
                    flattened.append(value)
                    continue
                start, end = int(match.group(2)), int(match.group(3))
                flattened.extend(
                    f"{match.group(1)}{index}{match.group(4)}"
                    for index in range(start, end + 1)
                )
            values = flattened
        if kwargs.get("materials"):
            values = [node for node in values if self.node_types.get(node) in {"lambert", "standardSurface", "dx11Shader"}]
        if kwargs.get("long"):
            return [self.long_names.get(node, node) for node in values]
        return values


def _bone(cmds: FakeCmds, joint: str, index: int, flags: int = 0) -> None:
    cmds.nodes.add(joint)
    cmds.descendants.append(joint)
    cmds.attrs.update(
        {
            (joint, "mmd_bone_name"): f"bone{index}",
            (joint, "mmd_bone_name_en"): f"Bone{index}",
            (joint, "mmd_bone_index"): index,
            (joint, "mmd_bone_parent_index"): -1 if index == 0 else 0,
            (joint, "mmd_pmx_rest_position"): [(float(index), 0.0, 0.0)],
            (joint, "mmd_deform_layer"): 0,
            (joint, "mmd_bone_flags"): flags,
        }
    )
    if flags & PmxBoneFlag.CONNECT_BONE:
        cmds.attrs[(joint, "mmd_connect_index")] = 0
        cmds.attrs[(joint, "mmd_connect_bone_index")] = 0
    else:
        cmds.attrs[(joint, "mmd_bone_offset")] = [(0.0, 1.0, 0.0)]
    if flags & (PmxBoneFlag.GRANT_PARENT_ROTATE | PmxBoneFlag.GRANT_PARENT_MOVE):
        cmds.attrs[(joint, "mmd_grant_parent_index")] = 0
        cmds.attrs[(joint, "mmd_grant_parent")] = "bone0"
        cmds.attrs[(joint, "mmd_grant_rate")] = 0.5
    if flags & PmxBoneFlag.AXIS_FIXED:
        cmds.attrs[(joint, "mmd_fixed_axis")] = [(1.0, 0.0, 0.0)]
        cmds.attrs[(joint, "mmd_axis_direction")] = [(1.0, 0.0, 0.0)]
    if flags & PmxBoneFlag.LOCAL_AXIS:
        cmds.attrs[(joint, "mmd_local_x_axis")] = [(1.0, 0.0, 0.0)]
        cmds.attrs[(joint, "mmd_x_axis_direction")] = [(1.0, 0.0, 0.0)]
        cmds.attrs[(joint, "mmd_local_z_axis")] = [(0.0, 0.0, 1.0)]
        cmds.attrs[(joint, "mmd_z_axis_direction")] = [(0.0, 0.0, 1.0)]
    if flags & PmxBoneFlag.EXTERNAL_PARENT_DEFORM:
        cmds.attrs[(joint, "mmd_external_parent_key")] = 42
    if flags & PmxBoneFlag.IK:
        cmds.attrs[(joint, "mmd_ik_target_index")] = 0
        cmds.attrs[(joint, "mmd_ik_target")] = "bone0"
        cmds.attrs[(joint, "mmd_ik_loop")] = 8
        cmds.attrs[(joint, "mmd_ik_limit_angle")] = 1.5
        cmds.attrs[(joint, "mmd_ik_links")] = json.dumps([{"bone": 0, "limit_enabled": False}])
    else:
        cmds.attrs.update(
            {
                (joint, "mmd_grant_parent"): "",
                (joint, "mmd_grant_rate"): 1.0,
                (joint, "mmd_fixed_axis"): [(0.0, 0.0, 1.0)],
                (joint, "mmd_local_x_axis"): [(1.0, 0.0, 0.0)],
                (joint, "mmd_local_z_axis"): [(0.0, 0.0, 1.0)],
                (joint, "mmd_external_parent_key"): -1,
                (joint, "mmd_ik_target"): "",
                (joint, "mmd_ik_loop"): 10,
                (joint, "mmd_ik_limit_angle"): 2.0,
                (joint, "mmd_ik_links"): "[]",
            }
        )


def _backend() -> tuple[FakeCmds, MayaSceneMetadataBackend]:
    cmds = FakeCmds()
    cmds.attrs.update(
        {
            ("|root", "mmd_model_name"): "モデル",
            ("|root", "mmd_model_name_en"): "Model",
            ("|root", "mmd_comment"): "コメント",
            ("|root", "mmd_comment_en"): "Comment",
        }
    )
    return cmds, MayaSceneMetadataBackend(cmds)


def test_display_frames_write_commits_exact_existing_payload() -> None:
    cmds, backend = _backend()
    plug = ("|root", "mmd_display_frames_json")
    cmds.attrs[plug] = "old"

    backend.begin_display_frames_write("|root")
    backend.apply_display_frames_write("|root", "new")
    backend.commit_display_frames_write("|root", "new")

    assert cmds.attrs[plug] == "new"
    assert cmds.undo_chunk_open is False
    assert cmds.undo_count == 0


def test_display_frames_write_rolls_back_new_attr_after_set_failure() -> None:
    cmds, backend = _backend()
    cmds.fail_set_path = "|root.mmd_display_frames_json"

    backend.begin_display_frames_write("|root")
    with pytest.raises(MayaSceneMetadataError, match="injected set failure"):
        backend.apply_display_frames_write("|root", "new")
    backend.rollback_write("|root")

    assert ("|root", "mmd_display_frames_json") not in cmds.attrs
    assert cmds.undo_chunk_open is False
    assert cmds.undo_count == 1


def test_display_frames_write_empty_failed_chunk_does_not_undo_prior_action() -> None:
    cmds, backend = _backend()
    plug = ("|root", "mmd_display_frames_json")
    cmds.attrs[plug] = "old"
    cmds.fail_set_path = "|root.mmd_display_frames_json"

    backend.begin_display_frames_write("|root")
    with pytest.raises(MayaSceneMetadataError, match="injected set failure"):
        backend.apply_display_frames_write("|root", "new")
    backend.rollback_write("|root")

    assert cmds.attrs[plug] == "old"
    assert cmds.undo_chunk_open is False
    assert cmds.undo_count == 0


def test_display_frames_write_readback_failure_restores_existing_preimage() -> None:
    cmds, backend = _backend()
    plug = ("|root", "mmd_display_frames_json")
    cmds.attrs[plug] = "old"
    cmds.ignore_set_path = "|root.mmd_display_frames_json"

    backend.begin_display_frames_write("|root")
    backend.apply_display_frames_write("|root", "new")
    with pytest.raises(MayaSceneMetadataError, match="readback mismatch"):
        backend.commit_display_frames_write("|root", "new")
    backend.rollback_write("|root")

    assert cmds.attrs[plug] == "old"
    assert cmds.undo_count == 1


def _material(cmds: FakeCmds, shader: str, index: int = 0, *, shared_toon: int = 0) -> None:
    cmds.nodes.add(shader)
    cmds.node_types[shader] = "standardSurface"
    cmds.attrs.update(
        {
            (shader, "mmd_material"): 1,
            (shader, "mmd_material_index"): index,
            (shader, "mmd_material_name"): "マテリアル",
            (shader, "mmd_material_name_en"): "Material",
            (shader, "diffuse_color"): [(0.1, 0.2, 0.3)],
            (shader, "mmd_diffuse_alpha"): 0.75,
            (shader, "specular_color"): [(0.4, 0.5, 0.6)],
            (shader, "shininess"): 12.5,
            (shader, "ambient_color"): [(0.01, 0.02, 0.03)],
            (shader, "mmd_draw_flags"): 3,
            (shader, "mmd_edge_color"): [(0.7, 0.8, 0.9)],
            (shader, "mmd_edge_alpha"): 0.5,
            (shader, "mmd_edge_size"): 1.25,
            (shader, "mmd_sphere_mode"): 2,
            (shader, "mmd_shared_toon_flag"): shared_toon,
            (shader, "mmd_toon_texture_index"): 4,
            (shader, "mmd_memo"): "memo",
        }
    )


def _registry(cmds: FakeCmds, *, morph_members: list[str] | None = None) -> None:
    cmds.nodes.add("registry")
    cmds.node_types["registry"] = "network"
    cmds.attrs.update(
        {
            ("|root", "mmd_model_registry"): True,
            ("registry", "mmd_model_registry_schema"): "1",
            ("registry", "modelRoot"): True,
            ("registry", "morphMembers"): True,
        }
    )
    cmds.connections[("|root.mmd_model_registry", None)] = ["registry"]
    cmds.connections[("registry.modelRoot", None)] = ["|root"]
    cmds.connections[("registry.morphMembers", None)] = list(morph_members or [])


def _morph(
    cmds: FakeCmds,
    node: str,
    morph_type: str,
    offsets: list[dict[str, Any]],
    *,
    index: int = 0,
    legacy_root: bool = False,
) -> None:
    attr_by_type = {
        "vertex": "mmd_vertex_morph_offsets_raw_json",
        "bone": "mmd_bone_morph_offsets_raw_json",
        "group": "mmd_group_morph_offsets_json",
        "material": "mmd_material_morph_offsets_json",
        "uv": "mmd_uv_morph_offsets_json",
        "additional_uv1": "mmd_uv_morph_offsets_json",
        "additional_uv2": "mmd_uv_morph_offsets_json",
        "additional_uv3": "mmd_uv_morph_offsets_json",
        "additional_uv4": "mmd_uv_morph_offsets_json",
        "flip": "mmd_flip_morph_offsets_json",
        "impulse": "mmd_impulse_morph_offsets_json",
    }
    cmds.nodes.add(node)
    cmds.node_types[node] = "network"
    cmds.attrs.update(
        {
            (node, "mmd_morph_name"): f"モーフ{index}",
            (node, "mmd_morph_name_en"): f"Morph{index}",
            (node, "mmd_morph_type"): morph_type,
            (node, "mmd_morph_index"): index,
            (node, "mmd_morph_panel"): 4,
            (node, attr_by_type[morph_type]): json.dumps(offsets, ensure_ascii=False),
        }
    )
    if legacy_root:
        cmds.attrs[(node, "mmd_model_root")] = True
        cmds.connections[(f"{node}.mmd_model_root", None)] = ["|root"]


def _vertex_scene(
    *,
    source_mapping: Any = None,
    parent_source_mapping: Any = None,
    vertex_count: int = 2,
    destination: str = "bs.weight[3]",
    alias_pairs: list[str] | None = None,
) -> tuple[FakeCmds, MayaSceneMetadataBackend]:
    cmds, backend = _backend()
    _morph(cmds, "morph", "vertex", [])
    _registry(cmds, morph_members=["morph"])
    cmds.nodes.update({"controller", "bs", "mesh", "meshTransform"})
    cmds.node_types.update(
        {
            "controller": "mmdMorphController",
            "bs": "blendShape",
            "mesh": "mesh",
            "meshTransform": "transform",
        }
    )
    cmds.attrs[("|root", "mmd_import_scale")] = 1.0
    cmds.connections[("|root.mmd_morph_controller", None)] = ["controller"]
    cmds.connections[("controller.outputWeight[0]", None)] = [destination]
    cmds.aliases["bs"] = alias_pairs or ["モーフ0", "weight[3]"]
    cmds.attrs[("bs", "mmd_blendshape_morph_names_json")] = json.dumps(
        {"3": {"name": "モーフ0", "index": 0}}, ensure_ascii=False
    )
    cmds.attrs[("bs", "geometry")] = ["mesh"]
    cmds.attrs[("bs", "geometryIndices")] = [0]
    cmds.attrs[("mesh", "vertexCount")] = vertex_count
    cmds.attrs[("bs.inputTarget[0].inputTargetGroup[3]", "inputTargetItem")] = [6000]
    cmds.attrs[("bs.inputTarget[0].inputTargetGroup[3].inputTargetItem[6000]", "inputPointsTarget")] = [
        (1.0, 2.0, 3.0)
    ]
    cmds.attrs[("bs.inputTarget[0].inputTargetGroup[3].inputTargetItem[6000]", "inputComponentsTarget")] = [
        "vtx[1]"
    ]
    if source_mapping is not None:
        cmds.attrs[("mesh", "mmd_source_vertex_indices")] = source_mapping
    if parent_source_mapping is not None:
        cmds.parents["mesh"] = ["meshTransform"]
        cmds.attrs[("meshTransform", "mmd_source_vertex_indices")] = parent_source_mapping
    return cmds, backend


def test_unicode_model_and_ordinary_two_bone_metadata() -> None:
    cmds, backend = _backend()
    _bone(cmds, "|root|Skeleton|root", 0)
    _bone(cmds, "|root|Skeleton|child", 1)

    assert backend.read_model_metadata("|root")["name"] == "モデル"
    bones = list(backend.iter_bone_metadata("|root"))
    assert [(bone["index"], bone["binding_identity"]) for bone in bones] == [
        (0, "|root|Skeleton|root"),
        (1, "|root|Skeleton|child"),
    ]


def test_all_conditional_flags_and_aliases() -> None:
    cmds, backend = _backend()
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
    bone = list(backend.iter_bone_metadata("|root"))[0]
    assert bone["connect_bone_index"] == 0
    assert bone["grant_local"] is True
    assert bone["fixed_axis"] == (1.0, 0.0, 0.0)
    assert bone["local_axis_z"] == (0.0, 0.0, 1.0)
    assert bone["external_parent_key"] == 42
    assert bone["ik_links"] == [{"bone": 0, "limit_enabled": False}]


@pytest.mark.parametrize(
    ("change", "error"),
    [
        (lambda cmds: cmds.attrs.__setitem__(("|root|joint", "mmd_connect_bone_index"), 2), "conflicting"),
        (lambda cmds: cmds.attrs.__setitem__(("|root|joint", "mmd_axis_direction"), [(0.0, 1.0, 0.0)]), "conflicting"),
        (lambda cmds: cmds.attrs.__setitem__(("|root|joint", "mmd_ik_target"), "missing"), "unknown"),
    ],
)
def test_alias_conflicts_fail_closed(change: Any, error: str) -> None:
    cmds, backend = _backend()
    _bone(cmds, "|root|joint", 0, int(PmxBoneFlag.CONNECT_BONE | PmxBoneFlag.AXIS_FIXED | PmxBoneFlag.IK))
    change(cmds)
    with pytest.raises(MayaSceneMetadataError, match=error):
        list(backend.iter_bone_metadata("|root"))


@pytest.mark.parametrize(
    "flags,attr,value",
    [
        (0, "mmd_connect_index", 0),
        (0, "mmd_grant_rate", 0.5),
        (0, "mmd_fixed_axis", [(1.0, 0.0, 0.0)]),
        (0, "mmd_ik_loop", 1),
        (int(PmxBoneFlag.IK), "mmd_ik_links", "not json"),
        (int(PmxBoneFlag.IK), "mmd_ik_links", "{}"),
    ],
)
def test_missing_or_stale_flag_payloads_fail_closed(flags: int, attr: str, value: Any) -> None:
    cmds, backend = _backend()
    _bone(cmds, "|root|joint", 0, flags)
    cmds.attrs[("|root|joint", attr)] = value
    with pytest.raises(MayaSceneMetadataError):
        list(backend.iter_bone_metadata("|root"))


def test_bone_presenter_inactive_defaults_are_allowed() -> None:
    cmds, backend = _backend()
    _bone(cmds, "|root|joint", 0)

    assert list(backend.iter_bone_metadata("|root"))[0]["ik_links"] == []


def test_bone_presenter_tail_default_is_allowed_for_index_connection() -> None:
    cmds, backend = _backend()
    _bone(cmds, "|root|joint", 0, int(PmxBoneFlag.CONNECT_BONE))
    cmds.attrs[("|root|joint", "mmd_bone_offset")] = [(0.0, -1.0, 0.0)]

    assert list(backend.iter_bone_metadata("|root"))[0]["connect_bone_index"] == 0

    cmds.attrs[("|root|joint", "mmd_bone_offset")] = [(0.0, 3.0, 0.0)]
    with pytest.raises(MayaSceneMetadataError, match="stale tail_offset"):
        list(backend.iter_bone_metadata("|root"))


def test_connect_bone_missing_target_sentinel_is_allowed() -> None:
    cmds, backend = _backend()
    _bone(cmds, "|root|terminal", 0, int(PmxBoneFlag.CONNECT_BONE))
    cmds.attrs[("|root|terminal", "mmd_connect_index")] = -1
    cmds.attrs[("|root|terminal", "mmd_connect_bone_index")] = -1

    assert list(backend.iter_bone_metadata("|root"))[0]["connect_bone_index"] == -1

    cmds.attrs[("|root|terminal", "mmd_connect_index")] = -2
    cmds.attrs[("|root|terminal", "mmd_connect_bone_index")] = -2
    with pytest.raises(MayaSceneMetadataError, match="must be >= -1"):
        list(backend.iter_bone_metadata("|root"))


def test_name_only_and_full_path_references_resolve_but_ambiguity_fails() -> None:
    cmds, backend = _backend()
    _bone(cmds, "|root|source", 0, int(PmxBoneFlag.IK))
    _bone(cmds, "|root|target", 1)
    cmds.attrs.pop(("|root|source", "mmd_ik_target_index"))
    cmds.attrs[("|root|source", "mmd_ik_target")] = "|root|target"

    assert list(backend.iter_bone_metadata("|root"))[0]["ik_target_index"] == 1

    cmds.attrs[("|root|target", "mmd_bone_name_en")] = "bone0"
    cmds.attrs[("|root|source", "mmd_ik_target")] = "bone0"
    with pytest.raises(MayaSceneMetadataError, match="ambiguous"):
        list(backend.iter_bone_metadata("|root"))


@pytest.mark.parametrize(
    "attr,value",
    [
        ("mmd_bone_index", True),
        ("mmd_deform_layer", float("nan")),
        ("mmd_pmx_rest_position", [(0.0, 1.0)]),
        ("mmd_pmx_rest_position", [(0.0, True, 1.0)]),
    ],
)
def test_strict_scalar_and_vector_validation(attr: str, value: Any) -> None:
    cmds, backend = _backend()
    _bone(cmds, "|root|joint", 0)
    cmds.attrs[("|root|joint", attr)] = value
    with pytest.raises(MayaSceneMetadataError):
        list(backend.iter_bone_metadata("|root"))


def test_duplicate_binding_or_index_and_missing_root_fail() -> None:
    cmds, backend = _backend()
    _bone(cmds, "|root|joint", 0)
    cmds.descendants.append("|root|joint")
    with pytest.raises(MayaSceneMetadataError, match="duplicate joint binding"):
        list(backend.iter_bone_metadata("|root"))

    cmds, backend = _backend()
    _bone(cmds, "|root|a", 0)
    _bone(cmds, "|root|b", 0)
    with pytest.raises(MayaSceneMetadataError, match="duplicate mmd_bone_index"):
        list(backend.iter_bone_metadata("|root"))
    with pytest.raises(MayaSceneMetadataError, match="does not exist"):
        backend.read_model_metadata("|missing")


def test_legacy_material_discovery_maps_unicode_semantics_and_deduplicates_binding() -> None:
    cmds, backend = _backend()
    _material(cmds, "mat", shared_toon=0)
    cmds.meshes.extend(["|root|Geometry|meshShape", "|root|Geometry|meshShape2"])
    cmds.nodes.update(cmds.meshes)
    for mesh in cmds.meshes:
        cmds.node_types[mesh] = "mesh"
        cmds.connections[(mesh, "shadingEngine")] = ["sg"]
    cmds.node_types["sg"] = "shadingEngine"
    cmds.connections[("sg", None)] = ["mat"]

    materials = list(backend.iter_material_metadata("|root"))
    assert materials == [
        {
            "name": "マテリアル",
            "name_english": "Material",
            "index": 0,
            "diffuse": (0.1, 0.2, 0.3, 0.75),
            "specular": (0.4, 0.5, 0.6),
            "specular_coefficient": 12.5,
            "ambient": (0.01, 0.02, 0.03),
            "draw_flags": 3,
            "edge_color": (0.7, 0.8, 0.9, 0.5),
            "edge_size": 1.25,
            "texture_path": None,
            "resolved_texture_path": None,
            "sphere_texture_path": None,
            "resolved_sphere_texture_path": None,
            "sphere_mode": 2,
            "shared_toon": False,
            "toon_texture_index": 4,
            "toon_texture_path": None,
            "resolved_toon_texture_path": None,
            "memo": "memo",
            "binding_identity": "mat",
        }
    ]


def test_registry_success_and_invalid_registry_never_falls_back() -> None:
    cmds, backend = _backend()
    _material(cmds, "registryMat")
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
    cmds.connections[("registry.materialMembers", None)] = ["registryMat"]

    assert list(backend.iter_material_metadata("|root"))[0]["index"] == 0

    cmds.attrs[("registry", "mmd_model_registry_schema")] = "broken"
    with pytest.raises(MayaSceneMetadataError, match="unsupported registry schema"):
        list(backend.iter_material_metadata("|root"))


def test_registry_root_comparison_canonicalizes_short_and_long_paths() -> None:
    cmds, backend = _backend()
    _material(cmds, "registryMat")
    _morph(cmds, "registryMorph", "group", [])
    _registry(cmds, morph_members=["registryMorph"])
    cmds.attrs[("registry", "materialMembers")] = True
    cmds.connections[("registry.materialMembers", None)] = ["registryMat"]

    cmds.nodes.add("root")
    cmds.node_types["root"] = "transform"
    cmds.long_names["root"] = "|root"
    cmds.attrs[("root", "mmd_model_registry")] = True
    cmds.connections[("root.mmd_model_registry", None)] = ["registry"]

    assert list(backend.iter_material_metadata("root"))[0]["index"] == 0
    assert list(backend.iter_morph_metadata("root"))[0]["binding_identity"] == "registryMorph"


def test_valid_pre_material_registry_uses_bounded_legacy_fallback() -> None:
    cmds, backend = _backend()
    _material(cmds, "legacyMat")
    cmds.nodes.add("registry")
    cmds.node_types["registry"] = "network"
    cmds.attrs.update(
        {
            ("|root", "mmd_model_registry"): True,
            ("registry", "mmd_model_registry_schema"): "1",
            ("registry", "modelRoot"): True,
        }
    )
    cmds.connections[("|root.mmd_model_registry", None)] = ["registry"]
    cmds.connections[("registry.modelRoot", None)] = ["|root"]
    cmds.meshes.append("|root|mesh")
    cmds.node_types["|root|mesh"] = "mesh"
    cmds.node_types["sg"] = "shadingEngine"
    cmds.connections[("|root|mesh", "shadingEngine")] = ["sg"]
    cmds.connections[("sg", None)] = ["legacyMat"]

    assert list(backend.iter_material_metadata("|root"))[0]["name"] == "マテリアル"


def test_material_source_and_resolved_paths_use_file_provenance_only() -> None:
    cmds, backend = _backend()
    _material(cmds, "mat")
    cmds.meshes.append("|root|mesh")
    cmds.node_types["|root|mesh"] = "mesh"
    cmds.node_types["sg"] = "shadingEngine"
    cmds.connections[("|root|mesh", "shadingEngine")] = ["sg"]
    cmds.connections[("sg", None)] = ["mat"]
    cmds.attrs[("mat", "mmd_texture_path")] = "textures/画像.png"
    cmds.attrs[("mat", "mmd_resolved_texture_path")] = "C:/resolved/画像.png"
    cmds.nodes.add("file")
    cmds.node_types["file"] = "file"
    cmds.attrs.update(
        {
            ("file", "mmd_original_texture_path"): "textures/画像.png",
            ("file", "fileTextureName"): "C:/resolved/画像.png",
        }
    )
    cmds.connections[("mat.mmd_texture_path", "file")] = ["file"]
    assert list(backend.iter_material_metadata("|root"))[0]["texture_path"] == "textures/画像.png"
    assert list(backend.iter_material_metadata("|root"))[0]["resolved_texture_path"] == "C:/resolved/画像.png"

    cmds.nodes.add("file2")
    cmds.node_types["file2"] = "file"
    cmds.attrs.update(
        {
            ("file2", "mmd_original_texture_path"): "textures/画像.png",
            ("file2", "fileTextureName"): "C:/resolved/画像-2.png",
        }
    )
    cmds.connections[("mat.mmd_texture_path", "file")] = ["file", "file2"]
    with pytest.raises(MayaSceneMetadataError, match="ambiguous"):
        list(backend.iter_material_metadata("|root"))


def test_material_provenance_does_not_alias_another_hardware_texture_slot() -> None:
    """A main-texture file must not become custom-toon provenance by path match."""
    cmds, backend = _backend()
    _material(cmds, "mat", shared_toon=0)
    cmds.node_types["mat"] = "dx11Shader"
    cmds.attrs.update(
        {
            ("mat", "mmd_texture_path"): "textures/shared.png",
            ("mat", "mmd_toon_path"): "textures/shared.png",
        }
    )
    cmds.nodes.add("mainFile")
    cmds.node_types["mainFile"] = "file"
    cmds.attrs.update(
        {
            ("mainFile", "mmd_original_texture_path"): "textures/shared.png",
            ("mainFile", "fileTextureName"): "C:/resolved/shared.png",
        }
    )
    cmds.connections[("mat.MainTexture", "file")] = ["mainFile"]

    material = backend._read_material("mat")

    assert material["resolved_texture_path"] == "C:/resolved/shared.png"
    assert material["resolved_toon_texture_path"] is None


def test_material_source_path_restores_slot_file_original_provenance() -> None:
    """A resolved shader attr still exports the PMX source path from its file node."""
    cmds, backend = _backend()
    _material(cmds, "mat", shared_toon=0)
    cmds.node_types["mat"] = "dx11Shader"
    cmds.attrs[("mat", "mmd_texture_path")] = "C:/resolved/shared.png"
    cmds.nodes.add("mainFile")
    cmds.node_types["mainFile"] = "file"
    cmds.attrs.update(
        {
            ("mainFile", "mmd_original_texture_path"): "textures/shared.png",
            ("mainFile", "fileTextureName"): "C:/resolved/shared.png",
        }
    )
    cmds.connections[("mat.MainTexture", "file")] = ["mainFile"]

    material = backend._read_material("mat")

    assert material["texture_path"] == "textures/shared.png"
    assert material["resolved_texture_path"] == "C:/resolved/shared.png"


def test_material_explicit_resolved_paths_fallback_without_file_provenance() -> None:
    cmds, backend = _backend()
    _material(cmds, "mat")
    cmds.meshes.append("|root|mesh")
    cmds.node_types["|root|mesh"] = "mesh"
    cmds.node_types["sg"] = "shadingEngine"
    cmds.connections[("|root|mesh", "shadingEngine")] = ["sg"]
    cmds.connections[("sg", None)] = ["mat"]
    cmds.attrs.update(
        {
            ("mat", "mmd_texture_path"): "textures/main.png",
            ("mat", "mmd_resolved_texture_path"): "C:/resolved/main.png",
            ("mat", "mmd_sphere_path"): "textures/sphere.sph",
            ("mat", "mmd_resolved_sphere_texture_path"): "C:/resolved/sphere.sph",
            ("mat", "mmd_toon_path"): "textures/toon.png",
            ("mat", "mmd_resolved_toon_texture_path"): "C:/resolved/toon.png",
        }
    )

    material = list(backend.iter_material_metadata("|root"))[0]

    assert material["texture_path"] == "textures/main.png"
    assert material["resolved_texture_path"] == "C:/resolved/main.png"
    assert material["sphere_texture_path"] == "textures/sphere.sph"
    assert material["resolved_sphere_texture_path"] == "C:/resolved/sphere.sph"
    assert material["toon_texture_path"] == "textures/toon.png"
    assert material["resolved_toon_texture_path"] == "C:/resolved/toon.png"


def test_material_file_provenance_conflicting_with_explicit_path_fails_closed() -> None:
    cmds, backend = _backend()
    _material(cmds, "mat")
    cmds.meshes.append("|root|mesh")
    cmds.node_types["|root|mesh"] = "mesh"
    cmds.node_types["sg"] = "shadingEngine"
    cmds.connections[("|root|mesh", "shadingEngine")] = ["sg"]
    cmds.connections[("sg", None)] = ["mat"]
    cmds.attrs.update(
        {
            ("mat", "mmd_texture_path"): "textures/main.png",
            ("mat", "mmd_resolved_texture_path"): "C:/resolved/explicit.png",
            ("file", "mmd_original_texture_path"): "textures/main.png",
            ("file", "fileTextureName"): "C:/resolved/provenance.png",
        }
    )
    cmds.nodes.add("file")
    cmds.node_types["file"] = "file"
    cmds.connections[("mat.mmd_texture_path", "file")] = ["file"]

    with pytest.raises(MayaSceneMetadataError, match="conflicts"):
        list(backend.iter_material_metadata("|root"))


def test_material_file_provenance_accepts_equivalent_maya_path_spelling() -> None:
    cmds, backend = _backend()
    _material(cmds, "mat")
    cmds.meshes.append("|root|mesh")
    cmds.node_types["|root|mesh"] = "mesh"
    cmds.node_types["sg"] = "shadingEngine"
    cmds.connections[("|root|mesh", "shadingEngine")] = ["sg"]
    cmds.connections[("sg", None)] = ["mat"]
    explicit = r"C:\resolved\sphere.sph"
    cmds.attrs.update(
        {
            ("mat", "mmd_sphere_path"): "textures/sphere.sph",
            ("mat", "mmd_resolved_sphere_texture_path"): explicit,
            ("file", "mmd_original_texture_path"): "textures/sphere.sph",
            ("file", "fileTextureName"): "C:/resolved/sphere.sph",
        }
    )
    cmds.nodes.add("file")
    cmds.node_types["file"] = "file"
    cmds.connections[("mat.mmd_sphere_path", "file")] = ["file"]

    material = list(backend.iter_material_metadata("|root"))[0]

    assert material["resolved_sphere_texture_path"] == explicit


def test_material_malformed_explicit_resolved_path_fails_closed() -> None:
    cmds, backend = _backend()
    _material(cmds, "mat")
    cmds.meshes.append("|root|mesh")
    cmds.node_types["|root|mesh"] = "mesh"
    cmds.node_types["sg"] = "shadingEngine"
    cmds.connections[("|root|mesh", "shadingEngine")] = ["sg"]
    cmds.connections[("sg", None)] = ["mat"]
    cmds.attrs[("mat", "mmd_resolved_texture_path")] = 42

    with pytest.raises(MayaSceneMetadataError, match="exact string"):
        list(backend.iter_material_metadata("|root"))


@pytest.mark.parametrize(
    "attr,value",
    [
        ("mmd_material", True),
        ("mmd_diffuse_alpha", float("nan")),
        ("diffuse_color", [(0.0, 1.0)]),
        ("mmd_sphere_mode", 4),
        ("mmd_shared_toon_flag", 2),
    ],
)
def test_material_malformed_semantics_fail_closed(attr: str, value: Any) -> None:
    cmds, backend = _backend()
    _material(cmds, "mat")
    cmds.meshes.append("|root|mesh")
    cmds.node_types["|root|mesh"] = "mesh"
    cmds.node_types["sg"] = "shadingEngine"
    cmds.connections[("|root|mesh", "shadingEngine")] = ["sg"]
    cmds.connections[("sg", None)] = ["mat"]
    cmds.attrs[("mat", attr)] = value
    with pytest.raises(MayaSceneMetadataError):
        list(backend.iter_material_metadata("|root"))


def test_shared_toon_builtin_index_does_not_require_toon_path() -> None:
    cmds, backend = _backend()
    _material(cmds, "mat", shared_toon=1)
    cmds.meshes.append("|root|mesh")
    cmds.node_types["|root|mesh"] = "mesh"
    cmds.node_types["sg"] = "shadingEngine"
    cmds.connections[("|root|mesh", "shadingEngine")] = ["sg"]
    cmds.connections[("sg", None)] = ["mat"]
    material = list(backend.iter_material_metadata("|root"))[0]
    assert material["shared_toon"] is True
    assert material["toon_texture_path"] is None


def test_shared_toon_rejects_stale_texture_path_payload() -> None:
    cmds, backend = _backend()
    _material(cmds, "mat", shared_toon=1)
    cmds.meshes.append("|root|mesh")
    cmds.node_types["|root|mesh"] = "mesh"
    cmds.node_types["sg"] = "shadingEngine"
    cmds.connections[("|root|mesh", "shadingEngine")] = ["sg"]
    cmds.connections[("sg", None)] = ["mat"]
    cmds.attrs[("mat", "mmd_toon_path")] = "textures/stale.png"

    with pytest.raises(MayaSceneMetadataError, match="shared toon"):
        list(backend.iter_material_metadata("|root"))


def test_duplicate_material_index_fails_closed() -> None:
    cmds, backend = _backend()
    _material(cmds, "mat0", 0)
    _material(cmds, "mat1", 0)
    cmds.meshes.append("|root|mesh")
    cmds.node_types["|root|mesh"] = "mesh"
    cmds.node_types["sg"] = "shadingEngine"
    cmds.connections[("|root|mesh", "shadingEngine")] = ["sg"]
    cmds.connections[("sg", None)] = ["mat0", "mat1"]
    with pytest.raises(MayaSceneMetadataError, match="duplicate mmd_material_index"):
        list(backend.iter_material_metadata("|root"))


@pytest.mark.parametrize(
    ("morph_type", "offset"),
    [
        ("bone", {"bone_index": 1, "translation": [1, 2, 3], "rotation": [0, 0, 0, 1]}),
        ("group", {"morph_index": 0, "morph_rate": 0.5}),
        (
            "material",
            {
                "material_index": -1,
                "operation_type": 1,
                "diffuse": [1, 1, 1, 1],
                "specular": [0, 0, 0],
                "specular_coefficient": 1,
                "ambient": [0, 0, 0],
                "edge_color": [0, 0, 0, 1],
                "edge_size": 0.5,
                "texture_factor": [1, 1, 1, 1],
                "sphere_texture_factor": [1, 1, 1, 1],
                "toon_texture_factor": [1, 1, 1, 1],
            },
        ),
        ("uv", {"vertex_index": 2, "uv_offset": [0.1, 0.2, 0.3, 0.4]}),
        ("additional_uv1", {"vertex_index": 2, "uv_offset": [0.1, 0.2, 0.3, 0.4]}),
        ("additional_uv2", {"vertex_index": 2, "uv_offset": [0.1, 0.2, 0.3, 0.4]}),
        ("additional_uv3", {"vertex_index": 2, "uv_offset": [0.1, 0.2, 0.3, 0.4]}),
        ("additional_uv4", {"vertex_index": 2, "uv_offset": [0.1, 0.2, 0.3, 0.4]}),
        ("flip", {"morph_index": 0, "flip_rate": 0.25}),
        ("impulse", {"rigid_body_index": 1, "impulse": [1, 2, 3], "torque": [4, 5, 6]}),
    ],
)
def test_registry_morphs_map_all_raw_offset_types(morph_type: str, offset: dict[str, Any]) -> None:
    cmds, backend = _backend()
    _morph(cmds, "morph", morph_type, [offset])
    _registry(cmds, morph_members=["morph"])

    metadata = list(backend.iter_morph_metadata("|root"))[0]
    spec = MmdMorphSpec.from_mapping(metadata)
    assert metadata["name"] == "モーフ0"
    assert metadata["morph_type"] == morph_type
    assert metadata["binding_identity"] == "morph"
    assert metadata["offsets"][0].keys() == offset.keys()
    assert spec.binding_identity == "morph"
    if morph_type in {"flip", "impulse"}:
        assert metadata["runtime_capability"] == "unsupported"
        assert metadata["loss_policy"] == "reject"
    else:
        assert metadata["runtime_capability"] == "supported"
        assert metadata["loss_policy"] == "none"


def test_legacy_morph_discovery_is_limited_to_exact_root_ownership() -> None:
    cmds, backend = _backend()
    _morph(cmds, "owned", "group", [{"morph_index": 0, "morph_rate": 1.0}], legacy_root=True)
    _morph(cmds, "unowned", "group", [{"morph_index": 0, "morph_rate": 1.0}])
    _morph(cmds, "other", "group", [{"morph_index": 0, "morph_rate": 1.0}])
    cmds.attrs[("other", "mmd_model_root")] = True
    cmds.nodes.add("|otherRoot")
    cmds.connections[("other.mmd_model_root", None)] = ["|otherRoot"]

    assert [item["binding_identity"] for item in backend.iter_morph_metadata("|root")] == ["owned"]


def test_vertex_blendshape_source_mapping_falls_back_to_identity() -> None:
    _cmds, backend = _vertex_scene()

    metadata = list(backend.iter_morph_metadata("|root"))[0]

    assert metadata["offsets"] == [
        {"vertex_index": 1, "position_offset": [1.0, 2.0, -3.0]}
    ]


def test_vertex_blendshape_offsets_canonicalize_signed_zero() -> None:
    cmds, backend = _vertex_scene()
    item = "bs.inputTarget[0].inputTargetGroup[3].inputTargetItem[6000]"
    cmds.attrs[(item, "inputPointsTarget")] = [(0.0, 0.25, 0.0)]

    offset = list(backend.iter_morph_metadata("|root"))[0]["offsets"][0]

    assert offset["position_offset"] == [0.0, 0.25, 0.0]
    assert all(math.copysign(1.0, value) > 0.0 for value in offset["position_offset"] if value == 0.0)


def test_vertex_blendshape_offsets_expand_compressed_component_ranges() -> None:
    cmds, backend = _vertex_scene(vertex_count=3)
    item = "bs.inputTarget[0].inputTargetGroup[3].inputTargetItem[6000]"
    cmds.attrs[(item, "inputPointsTarget")] = [
        (1.0, 0.0, 0.0),
        (0.0, 2.0, 0.0),
        (0.0, 0.0, 3.0),
    ]
    cmds.attrs[(item, "inputComponentsTarget")] = ["vtx[0:2]"]

    metadata = list(backend.iter_morph_metadata("|root"))[0]

    assert metadata["offsets"] == [
        {"vertex_index": 0, "position_offset": [1.0, 0.0, 0.0]},
        {"vertex_index": 1, "position_offset": [0.0, 2.0, 0.0]},
        {"vertex_index": 2, "position_offset": [0.0, 0.0, -3.0]},
    ]


def test_vertex_blendshape_alias_destination_resolves_weight_index() -> None:
    _cmds, backend = _vertex_scene(
        destination="bs.VertexAlias",
        alias_pairs=["VertexAlias", "weight[3]"],
    )

    metadata = list(backend.iter_morph_metadata("|root"))[0]

    assert metadata["offsets"][0]["vertex_index"] == 1


@pytest.mark.parametrize(
    ("entry_change", "expected"),
    [
        ({"name": "別名", "index": 0}, "stale_raw_name_mapping"),
        ({"name": "モーフ0", "index": 9}, "stale_raw_name_mapping"),
        (None, "stale_raw_name_mapping"),
    ],
)
def test_vertex_blendshape_raw_mapping_mismatch_fails_closed(
    entry_change: dict[str, Any] | None,
    expected: str,
) -> None:
    cmds, backend = _vertex_scene()
    mapping = {} if entry_change is None else {"3": entry_change}
    cmds.attrs[("bs", "mmd_blendshape_morph_names_json")] = json.dumps(
        mapping,
        ensure_ascii=False,
    )

    with pytest.raises(MayaSceneMetadataError, match=expected) as captured:
        list(backend.iter_morph_metadata("|root"))

    assert captured.value.__cause__ is not None


def test_vertex_blendshape_legacy_alias_fallback_logs_stable_warning(caplog: Any) -> None:
    cmds, backend = _vertex_scene()
    cmds.attrs.pop(("bs", "mmd_blendshape_morph_names_json"))
    cmds.aliases["bs"] = [sanitize_text("モーフ0"), "weight[3]"]

    metadata = list(backend.iter_morph_metadata("|root"))[0]

    assert metadata["offsets"][0]["vertex_index"] == 1
    assert "legacy_sanitized_alias_fallback" in caplog.text


def test_vertex_blendshape_source_mapping_reads_nested_parent_array() -> None:
    _cmds, backend = _vertex_scene(parent_source_mapping=((10, 20),))

    metadata = list(backend.iter_morph_metadata("|root"))[0]

    assert metadata["offsets"] == [
        {"vertex_index": 20, "position_offset": [1.0, 2.0, -3.0]}
    ]


@pytest.mark.parametrize("source_mapping", ([1], [1, 1], [True, 2], [1.0, 2.0]))
def test_vertex_blendshape_invalid_source_mapping_fails_closed(source_mapping: Any) -> None:
    _cmds, backend = _vertex_scene(source_mapping=source_mapping)

    with pytest.raises(MayaSceneMetadataError, match="source vertex"):
        list(backend.iter_morph_metadata("|root"))


def test_registry_morph_binding_and_index_duplicates_fail_closed() -> None:
    cmds, backend = _backend()
    _morph(cmds, "morph", "group", [{"morph_index": 0, "morph_rate": 1.0}])
    _registry(cmds, morph_members=["morph", "morph"])
    with pytest.raises(MayaSceneMetadataError, match="duplicate morph binding"):
        list(backend.iter_morph_metadata("|root"))

    cmds, backend = _backend()
    _morph(cmds, "a", "group", [], index=2)
    _morph(cmds, "b", "group", [], index=2)
    _registry(cmds, morph_members=["a", "b"])
    with pytest.raises(MayaSceneMetadataError, match="duplicate mmd_morph_index"):
        list(backend.iter_morph_metadata("|root"))


def test_vertex_network_without_blendshape_binding_fails_closed() -> None:
    cmds, backend = _backend()
    _morph(
        cmds,
        "legacyVertex",
        "vertex",
        [{"vertex_index": 1, "position_offset": [0, 0, 0]}],
        legacy_root=True,
    )
    cmds.attrs.pop(("legacyVertex", "mmd_vertex_morph_offsets_raw_json"))

    with pytest.raises(MayaSceneMetadataError, match="blendShape|controller"):
        list(backend.iter_morph_metadata("|root"))


def test_invalid_registry_never_falls_back_to_legacy_morph() -> None:
    cmds, backend = _backend()
    _morph(cmds, "morph", "group", [], legacy_root=True)
    _registry(cmds, morph_members=[])
    cmds.attrs[("registry", "mmd_model_registry_schema")] = "broken"

    with pytest.raises(MayaSceneMetadataError, match="unsupported registry schema"):
        list(backend.iter_morph_metadata("|root"))


def _topology_scene() -> tuple[FakeCmds, MayaSceneMetadataBackend]:
    cmds, backend = _backend()
    _morph(cmds, "groupMorph", "group", [{"morph_index": 1, "morph_rate": 0.5}], index=0)
    _morph(cmds, "boneMorph", "bone", [], index=1)
    _registry(cmds, morph_members=["groupMorph", "boneMorph"])
    cmds.nodes.add("controller")
    cmds.node_types["controller"] = "mmdMorphController"
    cmds.connections[("|root.mmd_morph_controller", None)] = ["controller"]
    cmds.attrs[("controller", "topologyVersion")] = 1
    cmds.attrs[("controller", "groupTopology")] = "{}"
    return cmds, backend


def test_morph_topology_inspection_is_read_only_and_reports_stale() -> None:
    cmds, backend = _topology_scene()
    before = deepcopy(cmds.attrs)

    inspection = backend.inspect_morph_topology("|root")

    assert inspection.repairable
    assert inspection.diagnostics[0].code == "stale"
    assert cmds.attrs == before
    assert cmds.write_history == []


def test_legacy_vertex_topology_inspection_uses_narrow_projection() -> None:
    cmds, backend = _backend()
    _morph(cmds, "legacyVertex", "vertex", [], legacy_root=True)
    cmds.nodes.add("controller")
    cmds.node_types["controller"] = "mmdMorphController"
    cmds.attrs[("|root", "mmd_morph_controller")] = True
    cmds.connections[("|root.mmd_morph_controller", None)] = ["controller"]
    cmds.attrs[("controller", "topologyVersion")] = 1
    cmds.attrs[("controller", "groupTopology")] = "{}"

    inspection = backend.inspect_morph_topology("|root")

    assert inspection.valid
    assert cmds.write_history == []


def test_morph_topology_repair_exact_readback_and_undo() -> None:
    cmds, backend = _topology_scene()
    expected = '{"1":[[0,0.5]]}'

    backend.begin_morph_topology_repair("|root", expected)
    backend.apply_morph_topology_repair("|root", expected)
    backend.commit_morph_topology_repair("|root", expected)

    assert cmds.attrs[("controller", "groupTopology")] == expected
    assert backend.inspect_morph_topology("|root").valid
    cmds.undo()
    assert cmds.attrs[("controller", "groupTopology")] == "{}"


def test_morph_topology_repair_readback_failure_rolls_back_once() -> None:
    cmds, backend = _topology_scene()
    expected = '{"1":[[0,0.5]]}'
    cmds.ignore_set_path = "controller.groupTopology"

    backend.begin_morph_topology_repair("|root", expected)
    backend.apply_morph_topology_repair("|root", expected)
    with pytest.raises(MayaSceneMetadataError, match="readback mismatch"):
        backend.commit_morph_topology_repair("|root", expected)
    backend.rollback_write("|root")

    assert cmds.undo_count == 1
    assert cmds.attrs[("controller", "groupTopology")] == "{}"


def test_morph_topology_repair_first_write_failure_does_not_undo_prior_action() -> None:
    cmds, backend = _topology_scene()
    expected = '{"1":[[0,0.5]]}'
    cmds.fail_set_path = "controller.topologyVersion"

    backend.begin_morph_topology_repair("|root", expected)
    with pytest.raises(MayaSceneMetadataError, match="injected set failure"):
        backend.apply_morph_topology_repair("|root", expected)
    backend.rollback_write("|root")

    assert cmds.undo_count == 0
    assert cmds.attrs[("controller", "groupTopology")] == "{}"


def _writable_scene() -> tuple[FakeCmds, MayaSceneMetadataBackend, SceneMetadataAdapter]:
    cmds, backend = _backend()
    _bone(cmds, "|root|joint", 0)
    _material(cmds, "mat")
    _morph(cmds, "morph", "group", [{"morph_index": 0, "morph_rate": 1.0}])
    _registry(cmds, morph_members=["morph"])
    cmds.attrs[("registry", "materialMembers")] = True
    cmds.connections[("registry.materialMembers", None)] = ["mat"]
    return cmds, backend, SceneMetadataAdapter(backend)


def test_transactional_write_updates_existing_bindings_and_verifies_fingerprint() -> None:
    cmds, _, adapter = _writable_scene()
    original = adapter.read_spec("|root")
    target = replace(
        original,
        model=replace(original.model, name="更新モデル"),
        bones=(replace(original.bones[0], name="更新ボーン"),),
        materials=(replace(original.materials[0], memo="updated", edge_size=2.0),),
        morphs=(replace(original.morphs[0], name="更新モーフ", panel=3),),
    )

    adapter.write_spec("|root", target)

    assert adapter.read_spec("|root").fingerprint() == target.fingerprint()
    assert cmds.undo_chunk_open is False
    assert cmds.attrs[("|root", "mmd_model_name")] == "更新モデル"
    assert cmds.attrs[("|root|joint", "mmd_bone_name")] == "更新ボーン"
    assert cmds.attrs[("mat", "mmd_memo")] == "updated"
    assert cmds.attrs[("morph", "mmd_morph_name")] == "更新モーフ"


def test_material_reindex_commit_verifies_without_full_metadata_hooks() -> None:
    cmds, backend, _adapter = _writable_scene()
    _material(cmds, "mat2", 1)
    cmds.connections[("registry.materialMembers", None)].append("mat2")
    cmds.attrs[("registry", "materialMembers")] = True

    with patch.object(SceneMetadataAdapter, "read_spec", side_effect=AssertionError("full read")):
        backend.begin_material_reindex("|root", 0, 1)
        writes_before = len(cmds.write_history)
        cmds.set_attr("mat.mmd_material_index", 1)
        cmds.set_attr("mat2.mmd_material_index", 0)
        backend.commit_material_reindex("|root", (0, 1))

    assert cmds.undo_chunk_open is False
    assert cmds.write_history[writes_before:] == [
        "mat.mmd_material_index",
        "mat2.mmd_material_index",
    ]


def test_material_reindex_commit_mismatch_rolls_back_original_spec() -> None:
    cmds, backend, _adapter = _writable_scene()
    _material(cmds, "mat2", 1)
    cmds.connections[("registry.materialMembers", None)].append("mat2")
    cmds.attrs[("registry", "materialMembers")] = True

    backend.begin_material_reindex("|root", 0, 1)
    cmds.set_attr("mat.mmd_material_index", 1)
    with pytest.raises(MayaSceneMetadataError, match="narrow-state mismatch"):
        backend.commit_material_reindex("|root", (0, 1))
    backend.rollback_write("|root")

    assert cmds.attrs[("mat", "mmd_material_index")] == 0
    assert cmds.attrs[("mat2", "mmd_material_index")] == 1
    assert cmds.undo_chunk_open is False


def test_narrow_material_reindex_avoids_full_spec_read_and_rolls_back() -> None:
    cmds, backend, adapter = _writable_scene()
    _material(cmds, "mat2", 1)
    _morph(cmds, "materialMorph", "material", [{"material_index": 0}])
    cmds.connections[("registry.morphMembers", None)].append("materialMorph")
    cmds.attrs[("registry", "materialMembers")] = True
    cmds.connections[("registry.materialMembers", None)] = ["mat", "mat2"]
    original_morph = cmds.attrs[("materialMorph", "mmd_material_morph_offsets_json")]

    with patch.object(SceneMetadataAdapter, "read_spec", side_effect=AssertionError("full read")):
        backend.begin_material_reindex("|root", 0, 1)
        cmds.set_attr("mat.mmd_material_index", 1)
        with pytest.raises(MayaSceneMetadataError, match="narrow-state mismatch"):
            backend.commit_material_reindex("|root", (0, 1))
        backend.rollback_write("|root")

    assert cmds.attrs[("mat", "mmd_material_index")] == 0
    assert cmds.attrs[("mat2", "mmd_material_index")] == 1
    assert cmds.attrs[("materialMorph", "mmd_material_morph_offsets_json")] == original_morph
    assert cmds.undo_chunk_open is False


def test_bone_optional_payloads_clear_and_can_be_reenabled_transactionally() -> None:
    cmds, _, adapter = _writable_scene()
    optional_flags = int(
        PmxBoneFlag.CONNECT_BONE
        | PmxBoneFlag.GRANT_PARENT_ROTATE
        | PmxBoneFlag.AXIS_FIXED
        | PmxBoneFlag.LOCAL_AXIS
        | PmxBoneFlag.EXTERNAL_PARENT_DEFORM
        | PmxBoneFlag.IK
    )
    _bone(cmds, "|root|optional", 1, optional_flags)
    original = adapter.read_spec("|root")
    cleared_bone = replace(
        original.bones[1],
        flags=0,
        connect_bone_index=None,
        tail_offset=(0.0, 1.0, 0.0),
        grant_parent_index=None,
        grant_ratio=0.0,
        fixed_axis=None,
        local_axis_x=None,
        local_axis_z=None,
        external_parent_key=None,
        ik_target_index=None,
        ik_loop_count=0,
        ik_limit_radian=None,
        ik_links=(),
    )
    cleared = replace(original, bones=(original.bones[0], cleared_bone))

    adapter.write_spec("|root", cleared)

    assert adapter.read_spec("|root").fingerprint() == cleared.fingerprint()
    for attr in (
        "mmd_connect_index",
        "mmd_connect_bone_index",
        "mmd_connection_bone",
        "mmd_grant_parent_index",
        "mmd_axis_direction",
        "mmd_x_axis_direction",
        "mmd_z_axis_direction",
        "mmd_ik_target_index",
    ):
        assert ("|root|optional", attr) not in cmds.attrs

    reenabled = replace(original, bones=(original.bones[0], original.bones[1]))
    adapter.write_spec("|root", reenabled)

    assert adapter.read_spec("|root").fingerprint() == reenabled.fingerprint()


def test_bone_optional_payloads_can_be_enabled_after_base_registration() -> None:
    cmds, _, adapter = _writable_scene()
    original = adapter.read_spec("|root")
    enabled_bone = replace(
        original.bones[0],
        flags=int(
            PmxBoneFlag.GRANT_PARENT_ROTATE
            | PmxBoneFlag.EXTERNAL_PARENT_DEFORM
            | PmxBoneFlag.IK
        ),
        grant_parent_index=0,
        grant_ratio=0.35,
        external_parent_key=37,
        ik_target_index=0,
        ik_loop_count=7,
        ik_limit_radian=0.5,
        ik_links=({"bone": 0, "limit_enabled": False},),
    )
    for attr in (
        "mmd_grant_rate",
        "mmd_external_parent_key",
        "mmd_ik_loop",
        "mmd_ik_limit_angle",
        "mmd_ik_links",
    ):
        cmds.attrs.pop(("|root|joint", attr), None)

    target = replace(original, bones=(enabled_bone,))
    adapter.write_spec("|root", target)

    assert adapter.read_spec("|root").fingerprint() == target.fingerprint()
    assert cmds.attrs[("|root|joint", "mmd_grant_rate")] == 0.35
    assert cmds.attrs[("|root|joint", "mmd_external_parent_key")] == 37
    assert cmds.attrs[("|root|joint", "mmd_ik_loop")] == 7
    assert cmds.attrs[("|root|joint", "mmd_ik_limit_angle")] == 0.5
    assert json.loads(cmds.attrs[("|root|joint", "mmd_ik_links")]) == [
        {"bone": 0, "limit_enabled": False}
    ]


def test_connect_bone_missing_target_sentinel_round_trips_transactionally() -> None:
    cmds, _, adapter = _writable_scene()
    original = adapter.read_spec("|root")
    terminal = replace(
        original.bones[0],
        flags=int(PmxBoneFlag.CONNECT_BONE),
        connect_bone_index=-1,
        tail_offset=None,
    )
    target = replace(original, bones=(terminal,))

    adapter.write_spec("|root", target)

    assert adapter.read_spec("|root").fingerprint() == target.fingerprint()
    assert cmds.attrs[("|root|joint", "mmd_connect_index")] == -1
    assert cmds.attrs[("|root|joint", "mmd_connect_bone_index")] == -1
    assert ("|root|joint", "mmd_connection_bone") not in cmds.attrs


@pytest.mark.parametrize("section", ["model", "bone", "material", "morph"])
def test_each_apply_hook_fails_closed_before_unsafe_changes(section: str) -> None:
    cmds, backend, adapter = _writable_scene()
    original = adapter.read_spec("|root")
    payload = original.to_mapping()
    backend.begin_write("|root")
    writes_before = len(cmds.write_history)
    try:
        if section == "model":
            payload["model"].pop("comment")
            with pytest.raises(MayaSceneMetadataError, match="fields mismatch"):
                backend.apply_model_metadata("|root", payload["model"])
        elif section == "bone":
            with pytest.raises(MayaSceneMetadataError, match="structural transaction"):
                backend.apply_bone_metadata("|root", [])
        elif section == "material":
            payload["materials"][0]["texture_path"] = "changed.png"
            with pytest.raises(MayaSceneMetadataError, match="texture path changes"):
                backend.apply_material_metadata("|root", payload["materials"])
        else:
            payload["morphs"][0]["offsets"] = []
            with pytest.raises(MayaSceneMetadataError, match="offsets changes"):
                backend.apply_morph_metadata("|root", payload["morphs"])
        assert len(cmds.write_history) == writes_before
    finally:
        backend.rollback_write("|root")
    assert adapter.read_spec("|root").fingerprint() == original.fingerprint()


def test_commit_fingerprint_mismatch_rolls_back_all_prior_sections() -> None:
    cmds, _, adapter = _writable_scene()
    original = adapter.read_spec("|root")
    target = replace(original, model=replace(original.model, name="ignored"))
    cmds.ignore_set_path = "|root.mmd_model_name"

    with pytest.raises(SceneMetadataError, match="fingerprint mismatch"):
        adapter.write_spec("|root", target)

    assert adapter.read_spec("|root").fingerprint() == original.fingerprint()
    assert cmds.undo_chunk_open is False


@pytest.mark.parametrize(
    "failed_path",
    [
        "|root.mmd_model_name_en",
        "|root|joint.mmd_bone_name_en",
        "mat.mmd_memo",
        "morph.mmd_morph_panel",
    ],
)
def test_set_failure_in_each_apply_section_rolls_back_partial_writes(failed_path: str) -> None:
    cmds, _, adapter = _writable_scene()
    original = adapter.read_spec("|root")
    target = replace(
        original,
        model=replace(original.model, name="model", name_english="model en"),
        bones=(replace(original.bones[0], name="bone", name_english="bone en"),),
        materials=(replace(original.materials[0], memo="material"),),
        morphs=(replace(original.morphs[0], panel=3),),
    )
    cmds.fail_set_path = failed_path

    with pytest.raises(SceneMetadataError, match="failed to write"):
        adapter.write_spec("|root", target)

    assert adapter.read_spec("|root").fingerprint() == original.fingerprint()
    assert cmds.undo_chunk_open is False


def test_explicit_rollback_restores_original_fingerprint() -> None:
    cmds, backend, adapter = _writable_scene()
    original = adapter.read_spec("|root")
    payload = original.to_mapping()["model"]
    payload["name"] = "temporary"

    backend.begin_write("|root")
    backend.apply_model_metadata("|root", payload)
    assert cmds.attrs[("|root", "mmd_model_name")] == "temporary"
    backend.rollback_write("|root")

    assert adapter.read_spec("|root").fingerprint() == original.fingerprint()
    assert cmds.attrs[("|root", "mmd_model_name")] == original.model.name


def test_begin_requires_undo_and_rejects_nested_transactions_without_opening_extra_chunk() -> None:
    cmds, backend, _ = _writable_scene()
    cmds.undo_enabled = False
    with pytest.raises(MayaSceneMetadataError, match="undo must be enabled"):
        backend.begin_write("|root")
    assert cmds.undo_chunk_open is False

    cmds.undo_enabled = True
    backend.begin_write("|root")
    with pytest.raises(MayaSceneMetadataError, match="already active"):
        backend.begin_write("|root")
    assert cmds.undo_chunk_open is True
    backend.rollback_write("|root")


def test_rebase_write_bindings_is_strict_read_only_and_single_use() -> None:
    cmds, backend, adapter = _writable_scene()
    original = adapter.read_spec("|root")

    backend.begin_write("|root")
    writes_before = len(cmds.write_history)
    backend.rebase_write_bindings("|root", original)
    assert len(cmds.write_history) == writes_before
    with pytest.raises(MayaSceneMetadataError, match="already been rebased"):
        backend.rebase_write_bindings("|root", original)
    backend.rollback_write("|root")


def test_rebase_write_bindings_rejects_binding_or_index_mismatch_without_writes() -> None:
    cmds, backend, adapter = _writable_scene()
    original = adapter.read_spec("|root")
    mismatched = replace(
        original,
        materials=(replace(original.materials[0], binding_identity="otherMaterial"),),
    )

    backend.begin_write("|root")
    writes_before = len(cmds.write_history)
    with pytest.raises(MayaSceneMetadataError, match="material binding/index set"):
        backend.rebase_write_bindings("|root", mismatched)
    assert len(cmds.write_history) == writes_before
    backend.rollback_write("|root")
