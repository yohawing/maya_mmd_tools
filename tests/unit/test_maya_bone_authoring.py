"""Tests for Maya-independent structural PMX bone authoring operations."""

from __future__ import annotations

import json
from typing import Any

import pytest

from mmd_tools.adapters.maya_bone_authoring import (
    MayaBoneAuthoringError,
    apply_bone_reindex,
    capture_rest_position,
    register_existing_joint,
    unregister_existing_joint,
)
from mmd_tools.core.constants import (
    ATTR_MMD_BONE_INDEX,
    ATTR_MMD_BONE_NAME,
    ATTR_MMD_BONE_PARENT_INDEX,
    ATTR_MMD_CONNECT_BONE_INDEX,
    ATTR_MMD_CONNECT_INDEX,
    ATTR_MMD_CONNECTION_BONE,
    ATTR_MMD_DISPLAY_FRAMES_JSON,
    ATTR_MMD_GRANT_PARENT_INDEX,
    ATTR_MMD_IK_LINKS,
    ATTR_MMD_IK_TARGET_INDEX,
    ATTR_MMD_MODEL_ROOT,
    ATTR_MMD_MODEL_REGISTRY,
    ATTR_MMD_REGISTRY_MORPH_MEMBERS,
    ATTR_MMD_REGISTRY_ROOT,
    ATTR_MMD_REGISTRY_SCHEMA,
)
from mmd_tools.core.model_authoring_spec import (
    MmdBoneSpec,
    MmdModelAuthoringSpec,
    MmdModelSpec,
    MmdMorphSpec,
)
from mmd_tools.core.pmx_data.bone import PmxBoneFlag


class FakeMayaCmds:
    """Small injected adapter that records every structural write."""

    def __init__(self) -> None:
        self.nodes = {"|モデル"}
        self.joints: list[str] = []
        self.other_descendants: list[str] = []
        self.attrs: dict[tuple[str, str], Any] = {}
        self.connections: dict[str, list[str]] = {}
        self.write_calls: list[str] = []
        self.delete_calls: list[str] = []
        self.world_translation: dict[str, list[float]] = {}

    def object_exists(self, node: str) -> bool:
        return node in self.nodes

    def list_relatives(self, node: str, **kwargs: Any) -> list[str]:
        assert node == "|モデル"
        if kwargs.get("type") == "joint":
            return list(self.joints)
        return [*self.joints, *self.other_descendants]

    def attribute_exists(self, attr: str, node: str) -> bool:
        return (node, attr) in self.attrs

    def get_attr(self, path: str) -> Any:
        node, attr = path.rsplit(".", 1)
        return self.attrs[(node, attr)]

    def add_attr(self, node: str, **kwargs: Any) -> None:
        attr = kwargs["longName"]
        self.attrs.setdefault((node, attr), None)

    def set_attr(self, path: str, *values: Any, **kwargs: Any) -> None:
        self.write_calls.append(path)
        node, attr = path.rsplit(".", 1)
        if kwargs.get("type") == "double3":
            self.attrs[(node, attr)] = tuple(float(value) for value in values)
        else:
            self.attrs[(node, attr)] = values[0]

    def delete_attr(self, path: str) -> None:
        self.delete_calls.append(path)
        node, attr = path.rsplit(".", 1)
        self.attrs.pop((node, attr), None)

    def list_connections(self, query: str, **kwargs: Any) -> list[str]:
        return list(self.connections.get(query, []))

    def ls(self, *nodes: str, **kwargs: Any) -> list[str]:
        if kwargs.get("type") == "network":
            return [node for node in self.nodes if node.startswith("|morph")]
        return list(nodes)

    def xform(self, node: str, **kwargs: Any) -> list[float]:
        if node not in self.world_translation:
            raise AssertionError("registration must not capture animated transforms")
        return list(self.world_translation[node])


def _bone(
    name: str,
    index: int,
    binding: str,
    *,
    parent: int = -1,
    flags: int = 0,
    connect: int | None = None,
    grant: int | None = None,
    target: int | None = None,
    links: tuple[dict[str, Any], ...] = (),
) -> MmdBoneSpec:
    return MmdBoneSpec(
        name=name,
        name_english=name,
        index=index,
        parent_index=parent,
        flags=flags,
        connect_bone_index=connect,
        grant_parent_index=grant,
        grant_ratio=0.5 if grant is not None else 0.0,
        ik_target_index=target,
        ik_loop_count=4 if target is not None else 0,
        ik_limit_radian=1.0 if target is not None else None,
        ik_links=links,
        binding_identity=binding,
    )


def _reindex_specs() -> tuple[MmdModelAuthoringSpec, MmdModelAuthoringSpec]:
    flags = int(PmxBoneFlag.CONNECT_BONE | PmxBoneFlag.GRANT_PARENT_ROTATE | PmxBoneFlag.IK)
    old_bones = (
        _bone("root", 0, "|モデル|root"),
        _bone(
            "child",
            1,
            "|モデル|child",
            parent=0,
            flags=flags,
            connect=0,
            grant=0,
            target=0,
            links=({"bone": 0},),
        ),
    )
    new_bones = (
        _bone("root", 1, "|モデル|root"),
        _bone(
            "child",
            0,
            "|モデル|child",
            parent=1,
            flags=flags,
            connect=1,
            grant=1,
            target=1,
            links=({"bone": 1},),
        ),
    )
    model = MmdModelSpec("モデル")
    old = MmdModelAuthoringSpec(
        model=model,
        bones=old_bones,
        morphs=(MmdMorphSpec("bone", morph_type="bone", offsets=({"bone_index": 1},), binding_identity="|morph"),),
    )
    new = MmdModelAuthoringSpec(
        model=model,
        bones=new_bones,
        morphs=(MmdMorphSpec("bone", morph_type="bone", offsets=({"bone_index": 0},), binding_identity="|morph"),),
    )
    return old, new


def _seed_reindex_scene(adapter: FakeMayaCmds) -> None:
    adapter.nodes.update({"|モデル|root", "|モデル|child", "|rigid", "|morph0"})
    adapter.joints[:] = ["|モデル|root", "|モデル|child"]
    adapter.other_descendants[:] = ["|rigid"]
    adapter.attrs.update(
        {
            ("|モデル|root", ATTR_MMD_BONE_INDEX): 0,
            ("|モデル|root", ATTR_MMD_BONE_NAME): "root",
            ("|モデル|root", ATTR_MMD_BONE_PARENT_INDEX): -1,
            ("|モデル|child", ATTR_MMD_BONE_INDEX): 1,
            ("|モデル|child", ATTR_MMD_BONE_NAME): "child",
            ("|モデル|child", ATTR_MMD_BONE_PARENT_INDEX): 0,
            ("|モデル|child", ATTR_MMD_CONNECT_INDEX): 0,
            ("|モデル|child", ATTR_MMD_CONNECT_BONE_INDEX): 0,
            ("|モデル|child", ATTR_MMD_GRANT_PARENT_INDEX): 0,
            ("|モデル|child", ATTR_MMD_IK_TARGET_INDEX): 0,
            ("|モデル|child", ATTR_MMD_IK_LINKS): json.dumps([{"bone": 0}]),
            ("|モデル", ATTR_MMD_DISPLAY_FRAMES_JSON): json.dumps(
                [{"name": "Root", "elements": [{"type": 0, "index": 0}, {"type": 1, "index": 0}]}]
            ),
            ("|rigid", "relatedBoneIndex"): 1,
            ("|morph0", "mmd_morph_type"): "bone",
            ("|morph0", "mmd_bone_morph_offsets_json"): json.dumps([{"bone_index": 1}]),
            ("|morph0", ATTR_MMD_MODEL_ROOT): True,
        }
    )
    adapter.connections["|morph0.mmd_model_root"] = ["|モデル"]


def test_register_writes_spec_and_never_reads_live_transform() -> None:
    adapter = FakeMayaCmds()
    joint = "|モデル|骨Δ"
    adapter.nodes.add(joint)
    adapter.joints.append(joint)
    bone = MmdBoneSpec(
        name="日本語骨",
        name_english="Unicode Bone",
        index=0,
        rest_position=(1.0, 2.0, 3.0),
        binding_identity=joint,
    )

    register_existing_joint("|モデル", bone, adapter)

    assert adapter.attrs[(joint, ATTR_MMD_BONE_INDEX)] == 0
    assert adapter.attrs[(joint, "mmd_bone_name")] == "日本語骨"
    assert adapter.attrs[(joint, "mmd_pmx_rest_position")] == (1.0, 2.0, 3.0)
    assert not adapter.world_translation


def test_register_accepts_connect_bone_missing_target_sentinel() -> None:
    adapter = FakeMayaCmds()
    joint = "|モデル|末端"
    adapter.nodes.add(joint)
    adapter.joints.append(joint)
    bone = MmdBoneSpec(
        name="末端",
        index=0,
        flags=int(PmxBoneFlag.CONNECT_BONE),
        connect_bone_index=-1,
        binding_identity=joint,
    )

    register_existing_joint("|モデル", bone, adapter)

    assert adapter.attrs[(joint, ATTR_MMD_CONNECT_INDEX)] == -1
    assert adapter.attrs[(joint, ATTR_MMD_CONNECT_BONE_INDEX)] == -1
    assert (joint, ATTR_MMD_CONNECTION_BONE) not in adapter.attrs


def test_register_rejects_duplicate_identity_without_writes() -> None:
    adapter = FakeMayaCmds()
    joint = "|モデル|骨"
    adapter.nodes.add(joint)
    adapter.joints.append(joint)
    adapter.attrs[(joint, ATTR_MMD_BONE_INDEX)] = 0
    before = dict(adapter.attrs)

    with pytest.raises(MayaBoneAuthoringError):
        register_existing_joint("|モデル", MmdBoneSpec("重複", binding_identity=joint), adapter)
    assert adapter.attrs == before
    assert adapter.write_calls == []


def test_capture_rest_position_keeps_effective_units_and_flips_z() -> None:
    adapter = FakeMayaCmds()
    joint = "|モデル|骨"
    adapter.nodes.add(joint)
    adapter.joints.append(joint)
    adapter.world_translation[joint] = [4.0, 6.0, -8.0]

    assert capture_rest_position("|モデル", joint, adapter) == (4.0, 6.0, 8.0)


def test_reindex_remaps_direct_ik_display_physics_and_morph_references() -> None:
    adapter = FakeMayaCmds()
    _seed_reindex_scene(adapter)
    old, new = _reindex_specs()

    apply_bone_reindex("|モデル", old, new, adapter)

    assert adapter.attrs[("|モデル|root", ATTR_MMD_BONE_INDEX)] == 1
    assert adapter.attrs[("|モデル|child", ATTR_MMD_BONE_INDEX)] == 0
    assert adapter.attrs[("|モデル|child", ATTR_MMD_BONE_PARENT_INDEX)] == 1
    assert adapter.attrs[("|モデル|child", ATTR_MMD_CONNECT_INDEX)] == 1
    assert adapter.attrs[("|モデル|child", ATTR_MMD_CONNECT_BONE_INDEX)] == 1
    assert adapter.attrs[("|モデル|child", ATTR_MMD_GRANT_PARENT_INDEX)] == 1
    assert adapter.attrs[("|モデル|child", ATTR_MMD_IK_TARGET_INDEX)] == 1
    assert json.loads(adapter.attrs[("|モデル|child", ATTR_MMD_IK_LINKS)])[0]["bone"] == 1
    display = json.loads(adapter.attrs[("|モデル", ATTR_MMD_DISPLAY_FRAMES_JSON)])
    assert display[0]["elements"][0]["index"] == 1
    assert adapter.attrs[("|rigid", "relatedBoneIndex")] == 0
    assert json.loads(adapter.attrs[("|morph0", "mmd_bone_morph_offsets_json")])[0]["bone_index"] == 0


def test_reindex_discovers_registry_owned_bone_morphs() -> None:
    adapter = FakeMayaCmds()
    _seed_reindex_scene(adapter)
    adapter.nodes.add("|registry")
    adapter.attrs.update(
        {
            ("|モデル", ATTR_MMD_MODEL_REGISTRY): True,
            ("|registry", ATTR_MMD_REGISTRY_SCHEMA): "1",
            ("|registry", ATTR_MMD_REGISTRY_ROOT): True,
            ("|registry", ATTR_MMD_REGISTRY_MORPH_MEMBERS): True,
        }
    )
    adapter.connections["|モデル.mmd_model_registry"] = ["|registry"]
    adapter.connections["|registry.modelRoot"] = ["|モデル"]
    adapter.connections["|registry.morphMembers"] = ["|morph0"]
    adapter.attrs.pop(("|morph0", ATTR_MMD_MODEL_ROOT))
    adapter.connections.pop("|morph0.mmd_model_root")
    old, new = _reindex_specs()

    apply_bone_reindex("|モデル", old, new, adapter)

    assert json.loads(adapter.attrs[("|morph0", "mmd_bone_morph_offsets_json")])[0]["bone_index"] == 0


def test_reindex_malformed_display_fails_before_any_write() -> None:
    adapter = FakeMayaCmds()
    _seed_reindex_scene(adapter)
    adapter.attrs[("|モデル", ATTR_MMD_DISPLAY_FRAMES_JSON)] = json.dumps(
        [{"name": "Root", "elements": [{"type": 0, "index": 99}]}]
    )
    old, new = _reindex_specs()
    before = dict(adapter.attrs)

    with pytest.raises(MayaBoneAuthoringError):
        apply_bone_reindex("|モデル", old, new, adapter)
    assert adapter.attrs == before
    assert adapter.write_calls == []


def test_unregister_rejects_reference_then_removes_metadata_without_deleting_joint() -> None:
    adapter = FakeMayaCmds()
    _seed_reindex_scene(adapter)
    adapter.attrs[("|モデル", ATTR_MMD_DISPLAY_FRAMES_JSON)] = json.dumps(
        [{"name": "Root", "elements": [{"type": 0, "index": 0}]}]
    )
    with pytest.raises(MayaBoneAuthoringError):
        unregister_existing_joint("|モデル", "|モデル|root", adapter)
    assert adapter.delete_calls == []

    adapter.attrs[("|モデル", ATTR_MMD_DISPLAY_FRAMES_JSON)] = json.dumps([{"name": "Root", "elements": []}])
    adapter.attrs[("|モデル|child", ATTR_MMD_BONE_PARENT_INDEX)] = -1
    for attr in (ATTR_MMD_CONNECT_INDEX, ATTR_MMD_CONNECT_BONE_INDEX, ATTR_MMD_GRANT_PARENT_INDEX, ATTR_MMD_IK_TARGET_INDEX):
        adapter.attrs.pop(("|モデル|child", attr), None)
    with pytest.raises(MayaBoneAuthoringError):
        unregister_existing_joint("|モデル", "|モデル|root", adapter)
    adapter.attrs[("|モデル|child", ATTR_MMD_IK_LINKS)] = "[]"
    adapter.attrs[("|rigid", "relatedBoneIndex")] = -1
    adapter.attrs[("|morph0", "mmd_bone_morph_offsets_json")] = "[]"

    unregister_existing_joint("|モデル", "|モデル|root", adapter)
    assert "|モデル|root" in adapter.nodes
    assert not adapter.attribute_exists(ATTR_MMD_BONE_INDEX, "|モデル|root")
    assert not adapter.attribute_exists(ATTR_MMD_BONE_NAME, "|モデル|root")
