"""Characterization contracts for the metadata-backend aggregate facade.

These tests intentionally exercise only the stable facade.  They protect its
semantic and undo boundaries while its implementation is split into repositories.
Only the smallest Model read freezes the exact adapter-call sequence.
"""

from __future__ import annotations

import inspect
from dataclasses import replace
from typing import Any

import pytest

from mmd_tools.adapters.maya_scene_metadata_backend import (
    MayaSceneMetadataBackend,
    MayaSceneMetadataError,
)
from mmd_tools.adapters.scene_metadata_adapter import SceneMetadataAdapter, SceneMetadataError
from tests.unit.test_maya_scene_metadata_backend import (
    FakeCmds,
    _backend,
    _writable_scene,
)


class _ReadTraceCmds(FakeCmds):
    """Record the exact adapter calls made by the smallest read surface."""

    def __init__(self) -> None:
        super().__init__()
        self.read_trace: list[tuple[Any, ...]] = []

    def object_exists(self, node: str) -> bool:
        self.read_trace.append(("object_exists", node))
        return super().object_exists(node)

    def attribute_exists(self, attr: str, node: str) -> bool:
        self.read_trace.append(("attribute_exists", node, attr))
        return super().attribute_exists(attr, node)

    def get_attr(self, path: str, **kwargs: Any) -> Any:
        self.read_trace.append(("get_attr", path, tuple(sorted(kwargs.items()))))
        return super().get_attr(path, **kwargs)


def test_facade_keeps_repository_facing_public_parameter_names() -> None:
    """Repository extraction must not change the injected backend protocol."""

    expected = {
        "read_model_metadata": ("self", "root"),
        "iter_bone_metadata": ("self", "root"),
        "iter_material_metadata": ("self", "root"),
        "iter_morph_metadata": ("self", "root"),
        "begin_display_frames_write": ("self", "model_root"),
        "apply_display_frames_write": ("self", "model_root", "payload"),
        "commit_display_frames_write": ("self", "model_root", "payload"),
        "begin_write": ("self", "model_root"),
        "rebase_write_bindings": ("self", "model_root", "target_spec"),
        "apply_model_metadata": ("self", "model_root", "metadata"),
        "apply_bone_metadata": ("self", "model_root", "metadata"),
        "apply_material_metadata": ("self", "model_root", "metadata"),
        "apply_morph_metadata": ("self", "model_root", "metadata"),
        "commit_write": ("self", "model_root"),
        "rollback_write": ("self", "model_root"),
    }

    actual = {
        name: tuple(inspect.signature(getattr(MayaSceneMetadataBackend, name)).parameters)
        for name in expected
    }

    assert actual == expected


def test_model_read_keeps_exact_adapter_call_order_and_error_boundary() -> None:
    cmds = _ReadTraceCmds()
    cmds.attrs.update(
        {
            ("|root", "mmd_model_name"): "モデル",
            ("|root", "mmd_model_name_en"): "Model",
            ("|root", "mmd_comment"): "コメント",
            ("|root", "mmd_comment_en"): "Comment",
        }
    )
    backend = MayaSceneMetadataBackend(cmds)

    assert backend.read_model_metadata("|root") == {
        "name": "モデル",
        "name_english": "Model",
        "comment": "コメント",
        "comment_english": "Comment",
    }
    assert cmds.read_trace == [
        ("object_exists", "|root"),
        ("attribute_exists", "|root", "mmd_model_name"),
        ("get_attr", "|root.mmd_model_name", ()),
        ("attribute_exists", "|root", "mmd_model_name_en"),
        ("get_attr", "|root.mmd_model_name_en", ()),
        ("attribute_exists", "|root", "mmd_comment"),
        ("get_attr", "|root.mmd_comment", ()),
        ("attribute_exists", "|root", "mmd_comment_en"),
        ("get_attr", "|root.mmd_comment_en", ()),
    ]

    cmds.attrs[("|root", "mmd_model_name")] = 123
    with pytest.raises(MayaSceneMetadataError, match="must be an exact string"):
        backend.read_model_metadata("|root")


def test_full_transaction_preserves_schema_semantics_and_rollback_fingerprint() -> None:
    cmds, _backend_facade, adapter = _writable_scene()
    original = adapter.read_spec("|root")
    target = replace(
        original,
        model=replace(original.model, name="更新モデル"),
        bones=(replace(original.bones[0], name="更新ボーン"),),
        materials=(replace(original.materials[0], memo="updated"),),
        morphs=(replace(original.morphs[0], name="更新モーフ"),),
    )
    payload = target.to_mapping()
    undo_events: list[tuple[str, str | None]] = []
    original_undo_info = cmds.undo_info
    original_undo = cmds.undo

    def traced_undo_info(**kwargs: Any) -> Any:
        if kwargs.get("openChunk"):
            undo_events.append(("open", kwargs.get("chunkName")))
        elif kwargs.get("closeChunk"):
            undo_events.append(("close", None))
        return original_undo_info(**kwargs)

    def traced_undo() -> None:
        undo_events.append(("undo", None))
        original_undo()

    cmds.undo_info = traced_undo_info
    cmds.undo = traced_undo

    assert payload["schema_version"] == 1
    cmds.ignore_set_path = "morph.mmd_morph_name"
    with pytest.raises(SceneMetadataError, match="fingerprint mismatch"):
        adapter.write_spec("|root", target)

    assert undo_events == [
        ("open", "MMD Authoring Metadata"),
        ("close", None),
        ("undo", None),
    ]
    assert (
        SceneMetadataAdapter(_backend_facade).read_spec("|root").fingerprint()
        == original.fingerprint()
    )


@pytest.mark.parametrize(
    "existing",
    (True, False),
)
def test_display_transaction_keeps_chunk_and_rollback_event_order(
    existing: bool,
) -> None:
    cmds, backend = _backend()
    plug = ("|root", "mmd_display_frames_json")
    if existing:
        cmds.attrs[plug] = "old"
    events: list[tuple[str, str | None]] = []
    original_undo_info = cmds.undo_info
    original_undo = cmds.undo

    def traced_undo_info(**kwargs: Any) -> Any:
        if kwargs.get("openChunk"):
            events.append(("open", kwargs.get("chunkName")))
        elif kwargs.get("closeChunk"):
            events.append(("close", None))
        return original_undo_info(**kwargs)

    def traced_undo() -> None:
        events.append(("undo", None))
        original_undo()

    cmds.undo_info = traced_undo_info
    cmds.undo = traced_undo

    backend.begin_display_frames_write("|root")
    backend.apply_display_frames_write("|root", "new")
    backend.rollback_write("|root")

    assert events == [
        ("open", "Edit Display Frames"),
        ("close", None),
        ("undo", None),
    ]
    assert cmds.undo_count == 1
    assert (cmds.attrs.get(plug) if existing else plug in cmds.attrs) == (
        "old" if existing else False
    )
