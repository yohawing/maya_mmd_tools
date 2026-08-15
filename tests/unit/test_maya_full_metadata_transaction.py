"""Direct lifecycle tests for the full metadata transaction authority."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from mmd_tools.adapters.maya_full_metadata_transaction import (
    MayaFullMetadataTransaction,
    MayaFullMetadataTransactionContext,
)
from mmd_tools.core.model_authoring_spec import (
    MmdBoneSpec,
    MmdMaterialSpec,
    MmdModelAuthoringSpec,
    MmdModelSpec,
    MmdMorphSpec,
)


def _spec(*, suffix: str = "") -> MmdModelAuthoringSpec:
    return MmdModelAuthoringSpec(
        model=MmdModelSpec(name=f"model{suffix}"),
        bones=(
            MmdBoneSpec(
                name=f"bone{suffix}",
                index=0,
                binding_identity=f"|root|bone{suffix}",
            ),
        ),
        materials=(
            MmdMaterialSpec(
                name=f"material{suffix}",
                index=0,
                binding_identity=f"|root|material{suffix}",
            ),
        ),
        morphs=(
            MmdMorphSpec(
                name=f"morph{suffix}",
                index=0,
                binding_identity=f"|root|morph{suffix}",
            ),
        ),
    )


class _FakeContext:
    def __init__(self) -> None:
        self.scene = _spec()
        self.active: dict[str, Any] | None = None
        self.undo_enabled = True
        self.chunk_open = False
        self.snapshot: MmdModelAuthoringSpec | None = None
        self.undo_count = 0
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def require_root(self, root: Any) -> None:
        if root != "|root":
            raise ValueError("root must be |root")

    def canonical_identity(self, root: str) -> str:
        return root

    def read_spec(self, _root: str) -> MmdModelAuthoringSpec:
        return self.scene

    def call_adapter(self, method: str, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((method, args, kwargs))
        if method == "undo_info" and kwargs.get("query") and kwargs.get("state"):
            return self.undo_enabled
        if method == "undo_info" and kwargs.get("openChunk"):
            assert not self.chunk_open
            self.snapshot = self.scene
            self.chunk_open = True
            return None
        if method == "undo_info" and kwargs.get("closeChunk"):
            assert self.chunk_open
            self.chunk_open = False
            return None
        if method == "undo":
            assert not self.chunk_open
            assert self.snapshot is not None
            self.scene = self.snapshot
            self.snapshot = None
            self.undo_count += 1
            return None
        raise AssertionError(f"unexpected adapter call: {method!r}, {kwargs!r}")

    def active_transaction(self, root: str) -> dict[str, Any]:
        if self.active is None:
            raise ValueError("no metadata write transaction is active")
        if root != self.active["root"]:
            raise ValueError("metadata write transaction belongs to another model root")
        return self.active

    def authority(self) -> MayaFullMetadataTransaction:
        context = MayaFullMetadataTransactionContext(
            error_factory=ValueError,
            require_root=self.require_root,
            canonical_identity=self.canonical_identity,
            read_spec=self.read_spec,
            call_adapter=self.call_adapter,
            get_active_transaction=lambda: self.active,
            set_active_transaction=self._set_active,
            active_transaction=self.active_transaction,
        )
        return MayaFullMetadataTransaction(context)

    def _set_active(self, transaction: dict[str, Any] | None) -> None:
        self.active = transaction


def test_begin_captures_preimage_bindings_and_opens_one_chunk() -> None:
    context = _FakeContext()
    context.authority().begin_write("|root")

    assert context.active is not None
    assert context.active["kind"] == "full_metadata"
    assert context.active["original_fingerprint"] == _spec().fingerprint()
    assert context.active["bone_bindings"] == {0: "|root|bone"}
    assert context.active["material_bindings"] == {0: "|root|material"}
    assert context.active["morph_bindings"] == {0: "|root|morph"}
    assert context.chunk_open is True
    assert [call[0] for call in context.calls] == ["undo_info", "undo_info"]
    assert context.calls[1][2] == {
        "openChunk": True,
        "chunkName": "MMD Authoring Metadata",
    }


def test_rebase_is_single_use_read_only_and_updates_all_bindings() -> None:
    context = _FakeContext()
    authority = context.authority()
    authority.begin_write("|root")
    rebased = _spec(suffix="-new")
    context.scene = rebased

    authority.rebase_write_bindings("|root", rebased)

    assert context.active is not None
    assert context.active["bindings_rebased"] is True
    assert context.active["target"] == rebased.to_mapping()
    assert context.active["bone_bindings"] == {0: "|root|bone-new"}
    assert context.active["material_bindings"] == {0: "|root|material-new"}
    assert context.active["morph_bindings"] == {0: "|root|morph-new"}
    assert context.active["mutated"] is True
    assert [call[0] for call in context.calls] == ["undo_info", "undo_info"]

    with pytest.raises(ValueError, match="already been rebased"):
        authority.rebase_write_bindings("|root", rebased)


def test_failed_rebase_rolls_back_completed_structural_mutation() -> None:
    context = _FakeContext()
    authority = context.authority()
    authority.begin_write("|root")
    context.scene = _spec(suffix="-actual")
    wrong_target = _spec(suffix="-target")

    with pytest.raises(ValueError, match="binding/index set does not match"):
        authority.rebase_write_bindings("|root", wrong_target)

    authority.rollback_write("|root")

    assert context.undo_count == 1
    assert context.scene.fingerprint() == _spec().fingerprint()
    assert context.active is None


def test_commit_mismatch_keeps_chunk_for_runner_rollback() -> None:
    context = _FakeContext()
    authority = context.authority()
    authority.begin_write("|root")
    context.scene = replace(context.scene, model=MmdModelSpec(name="tampered"))
    authority.mark_mutation()

    with pytest.raises(ValueError, match="fingerprint mismatch"):
        authority.commit_write("|root")

    assert context.active is not None
    assert context.chunk_open is True
    assert context.undo_count == 0

    authority.rollback_write("|root")
    assert context.active is None
    assert context.chunk_open is False
    assert context.undo_count == 1
    assert context.scene.fingerprint() == _spec().fingerprint()
    assert [call[0] for call in context.calls] == [
        "undo_info",
        "undo_info",
        "undo_info",
        "undo",
    ]


def test_empty_rollback_closes_owned_chunk_without_global_undo() -> None:
    context = _FakeContext()
    authority = context.authority()
    authority.begin_write("|root")

    authority.rollback_write("|root")

    assert context.active is None
    assert context.chunk_open is False
    assert context.undo_count == 0
    assert [call[0] for call in context.calls] == [
        "undo_info",
        "undo_info",
        "undo_info",
    ]


def test_commit_success_closes_chunk_and_clears_shared_state() -> None:
    context = _FakeContext()
    authority = context.authority()
    authority.begin_write("|root")

    authority.commit_write("|root")

    assert context.active is None
    assert context.chunk_open is False
    assert context.undo_count == 0


def test_begin_rejects_disabled_undo_without_active_state_or_chunk() -> None:
    context = _FakeContext()
    context.undo_enabled = False

    with pytest.raises(ValueError, match="undo must be enabled"):
        context.authority().begin_write("|root")

    assert context.active is None
    assert context.chunk_open is False
