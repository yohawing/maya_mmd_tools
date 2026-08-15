"""Full semantic metadata transaction authority for the Maya backend.

The backend remains the facade and owns the single shared transaction registry.
This authority only manages the full-spec transaction lifecycle; narrow
transactions continue to use their existing backend paths.
"""

from __future__ import annotations

from collections.abc import Callable, MutableMapping
from dataclasses import dataclass
from typing import Any

from mmd_tools.core.model_authoring_spec import MmdModelAuthoringSpec


ErrorFactory = Callable[[str], Exception]
RequireRoot = Callable[[Any], None]
CanonicalIdentity = Callable[[str], str]
ReadSpec = Callable[[str], MmdModelAuthoringSpec]
CallAdapter = Callable[..., Any]
Transaction = MutableMapping[str, Any]
GetActiveTransaction = Callable[[], Transaction | None]
SetActiveTransaction = Callable[[Transaction | None], None]
ActiveTransaction = Callable[[str], Transaction]


@dataclass(frozen=True)
class MayaFullMetadataTransactionContext:
    """Callbacks supplied by the facade/backend integration boundary."""

    error_factory: ErrorFactory
    require_root: RequireRoot
    canonical_identity: CanonicalIdentity
    read_spec: ReadSpec
    call_adapter: CallAdapter
    get_active_transaction: GetActiveTransaction
    set_active_transaction: SetActiveTransaction
    active_transaction: ActiveTransaction


class MayaFullMetadataTransaction:
    """Manage one full authoring-spec transaction without owning the registry."""

    _KIND = "full_metadata"

    def __init__(self, context: MayaFullMetadataTransactionContext) -> None:
        self._context = context

    def begin_write(self, model_root: str) -> None:
        """Capture the strict preimage and open the full metadata undo chunk."""
        context = self._context
        if context.get_active_transaction() is not None:
            raise context.error_factory("a metadata write transaction is already active")
        context.require_root(model_root)
        original = context.read_spec(model_root)
        canonical_root = context.canonical_identity(model_root)
        if not bool(context.call_adapter("undo_info", query=True, state=True)):
            raise context.error_factory("Maya undo must be enabled for metadata writes")
        transaction: Transaction = {
            "root": canonical_root,
            "kind": self._KIND,
            "original_fingerprint": original.fingerprint(),
            "target": original.to_mapping(),
            "bone_bindings": {
                bone.index: bone.binding_identity for bone in original.bones
            },
            "material_bindings": {
                material.index: material.binding_identity
                for material in original.materials
            },
            "morph_bindings": {
                morph.index: morph.binding_identity for morph in original.morphs
            },
            "bindings_rebased": False,
            "chunk_open": False,
            "mutated": False,
        }
        context.call_adapter(
            "undo_info", openChunk=True, chunkName="MMD Authoring Metadata"
        )
        transaction["chunk_open"] = True
        context.set_active_transaction(transaction)

    def require_active(self, model_root: str) -> Transaction:
        """Return the active full transaction for aggregate metadata hooks."""
        return self._full_transaction(model_root)

    def mark_mutation(self) -> None:
        """Declare that the next structural write belongs to this transaction.

        Structural writers call this at the first owned Maya mutation
        boundary, after their read-only preflight.  The Spec readback remains
        a commit check, not the only evidence that an Undo item is owned by
        this transaction: assignments, connections, and other derived scene
        state may be outside the Spec.
        """
        transaction = self._context.get_active_transaction()
        if transaction is not None and self._is_full_transaction(transaction):
            transaction["mutated"] = True

    def rebase_write_bindings(
        self,
        model_root: str,
        target_spec: MmdModelAuthoringSpec,
    ) -> None:
        """Adopt one structurally updated binding set using strict reads only."""
        context = self._context
        transaction = self._full_transaction(model_root)
        if transaction["bindings_rebased"]:
            raise context.error_factory("write bindings have already been rebased")
        if not isinstance(target_spec, MmdModelAuthoringSpec):
            raise context.error_factory("target_spec must be an MmdModelAuthoringSpec")

        # Coordinators call rebase only after the structural Maya writer has
        # completed.  Mark this boundary before strict post-structure reads so
        # a failed rebase still rolls back that owned structural mutation.
        self.mark_mutation()

        try:
            scene_spec = context.read_spec(model_root)
        except Exception as exc:
            raise context.error_factory(
                f"failed to read structurally updated bindings: {exc}"
            ) from exc

        sections = (
            ("bone", scene_spec.bones, target_spec.bones),
            ("material", scene_spec.materials, target_spec.materials),
            ("morph", scene_spec.morphs, target_spec.morphs),
        )
        rebased: dict[str, dict[int, str | None]] = {}
        for label, scene_items, target_items in sections:
            scene_bindings = {
                item.index: item.binding_identity for item in scene_items
            }
            target_bindings = {
                item.index: item.binding_identity for item in target_items
            }
            if scene_bindings != target_bindings:
                raise context.error_factory(
                    f"{label} binding/index set does not match structural target: "
                    f"scene={scene_bindings!r}, target={target_bindings!r}"
                )
            rebased[label] = target_bindings

        # The strict post-structure scene becomes the baseline for the
        # aggregate writers that run after this hook.
        transaction["target"] = scene_spec.to_mapping()
        transaction["bone_bindings"] = rebased["bone"]
        transaction["material_bindings"] = rebased["material"]
        transaction["morph_bindings"] = rebased["morph"]
        transaction["bindings_rebased"] = True

    def commit_write(self, model_root: str) -> None:
        """Verify the final strict fingerprint and close a successful chunk."""
        context = self._context
        transaction = self._full_transaction(model_root)
        try:
            expected = MmdModelAuthoringSpec.from_mapping(
                transaction["target"]
            ).fingerprint()
            actual = context.read_spec(model_root).fingerprint()
        except Exception as exc:
            # A writer may have changed Maya state before its final write or
            # readback failed.  Do not let an unreadable post-write scene
            # suppress the Undo needed to restore the captured preimage.
            transaction["mutated"] = True
            raise context.error_factory(
                f"failed to verify metadata transaction: {exc}"
            ) from exc
        if actual != expected:
            # Keep both the active registry entry and open chunk intact.  The
            # transaction runner must be able to invoke rollback_write next.
            raise context.error_factory(
                f"metadata transaction fingerprint mismatch: expected {expected}, got {actual}"
            )
        context.call_adapter("undo_info", closeChunk=True)
        transaction["chunk_open"] = False
        context.set_active_transaction(None)

    def rollback_write(self, model_root: str) -> None:
        """Close, undo, clear active state, then validate the original image."""
        context = self._context
        transaction = self._full_transaction(model_root)
        # Structural writers run through adjacent adapters and therefore do
        # not pass through the aggregate writer setters that record a
        # mutation.  A writer can also perform one Maya write and then raise.
        # Reconcile the strict scene preimage while the owned chunk is still
        # open so that only an observed owned change enables global Undo.
        self._reconcile_scene_mutation(transaction)
        try:
            if transaction["chunk_open"]:
                context.call_adapter("undo_info", closeChunk=True)
                transaction["chunk_open"] = False
            if transaction["mutated"]:
                context.call_adapter("undo")
                transaction["mutated"] = False
        finally:
            # Keep the backend registry clear even when Maya's undo operation
            # itself fails, matching the existing full-transaction boundary.
            context.set_active_transaction(None)
        actual = context.read_spec(model_root).fingerprint()
        if actual != transaction["original_fingerprint"]:
            raise context.error_factory("metadata rollback fingerprint mismatch")

    def _reconcile_scene_mutation(self, transaction: Transaction) -> None:
        """Promote an observed structural preimage change to a mutation."""
        if transaction["mutated"]:
            return
        try:
            actual = self._context.read_spec(transaction["root"]).fingerprint()
        except Exception:
            # A partially-written scene cannot prove that no owned mutation
            # occurred.  Fail closed so rollback consumes the owned Undo item.
            transaction["mutated"] = True
            return
        if actual != transaction["original_fingerprint"]:
            transaction["mutated"] = True

    def _full_transaction(self, model_root: str) -> Transaction:
        transaction = self._context.active_transaction(model_root)
        if not self._is_full_transaction(transaction):
            raise self._context.error_factory(
                "active transaction is not a full metadata write"
            )
        return transaction

    def _is_full_transaction(self, transaction: Transaction) -> bool:
        return transaction.get("kind") == self._KIND


__all__ = [
    "MayaFullMetadataTransaction",
    "MayaFullMetadataTransactionContext",
]
