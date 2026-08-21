"""Narrow transaction authority for the model Info string fields."""

from __future__ import annotations

from collections.abc import Callable, Collection, MutableMapping
from dataclasses import dataclass
from typing import Any


ErrorFactory = Callable[[str], Exception]
CanonicalIdentity = Callable[[Any], str]
RequireRoot = Callable[[Any], None]
CallAdapter = Callable[..., Any]
HasAttribute = Callable[[str, str], bool]
GetActiveTransaction = Callable[[], MutableMapping[str, Any] | None]
SetActiveTransaction = Callable[[MutableMapping[str, Any] | None], None]
ActiveTransaction = Callable[[str], MutableMapping[str, Any]]


@dataclass(frozen=True)
class InfoMetadataSession:
    """Opaque Info-field edit identity fixed at focus-in."""

    root: str
    attr: str
    token: object


@dataclass(frozen=True)
class MayaInfoMetadataTransactionContext:
    """Callbacks supplied by the metadata backend facade."""

    error_factory: ErrorFactory
    string_attributes: Collection[str]
    canonical_identity: CanonicalIdentity
    require_root: RequireRoot
    call_adapter: CallAdapter
    has_attribute: HasAttribute
    get_active_transaction: GetActiveTransaction
    set_active_transaction: SetActiveTransaction
    active_transaction: ActiveTransaction


class MayaInfoMetadataTransaction:
    """Own one focus-spanning Info string transaction."""

    _KIND = "info_metadata"

    def __init__(self, context: MayaInfoMetadataTransactionContext) -> None:
        self._context = context

    def begin(self, model_root: str, attr: str) -> InfoMetadataSession:
        """Capture one canonical string target and open its undo chunk."""
        context = self._context
        if context.get_active_transaction() is not None:
            raise context.error_factory("a metadata write transaction is already active")
        if attr not in context.string_attributes:
            raise context.error_factory(
                f"unsupported Info metadata attribute: {attr!r}"
            )
        root = context.canonical_identity(model_root)
        context.require_root(root)
        if not context.has_attribute(root, attr):
            raise context.error_factory(
                f"missing Info metadata attribute: {root}.{attr}"
            )
        if bool(context.call_adapter("get_attr", f"{root}.{attr}", lock=True)):
            raise context.error_factory(f"locked Info metadata attribute: {root}.{attr}")
        if not bool(context.call_adapter("undo_info", query=True, state=True)):
            raise context.error_factory(
                "Maya undo must be enabled for Info metadata edits"
            )
        original = self._string_value(
            context.call_adapter("get_attr", f"{root}.{attr}"), root, attr
        )
        token = object()
        context.call_adapter("undo_info", openChunk=True, chunkName="MMD Info Edit")
        context.set_active_transaction(
            {
                "root": root,
                "kind": "info_metadata",
                "attr": attr,
                "token": token,
                "original_value": original,
                "target_value": original,
                "chunk_open": True,
                "mutated": False,
            }
        )
        return InfoMetadataSession(root=root, attr=attr, token=token)

    def apply(
        self, model_root: str, session: InfoMetadataSession, value: str
    ) -> bool:
        """Write and read back the fixed string target."""
        context = self._context
        transaction = self._active_session(model_root, session)
        expected = self._string_value(value, session.root, session.attr)
        try:
            context.call_adapter(
                "set_attr", f"{session.root}.{session.attr}", expected, type="string"
            )
        except Exception:
            actual = self._string_value(
                context.call_adapter(
                    "get_attr", f"{session.root}.{session.attr}"
                ),
                session.root,
                session.attr,
            )
            transaction["mutated"] = bool(transaction["mutated"]) or (
                actual != transaction["original_value"]
            )
            raise
        transaction["mutated"] = True
        actual = self._string_value(
            context.call_adapter("get_attr", f"{session.root}.{session.attr}"),
            session.root,
            session.attr,
        )
        if actual != expected:
            raise context.error_factory(
                f"Info metadata readback mismatch for {session.root}.{session.attr}"
            )
        transaction["target_value"] = expected
        return expected != transaction["original_value"]

    def commit(self, model_root: str, session: InfoMetadataSession) -> bool:
        """Verify the final string and close the owned undo chunk."""
        context = self._context
        transaction = self._active_session(model_root, session)
        actual = self._string_value(
            context.call_adapter("get_attr", f"{session.root}.{session.attr}"),
            session.root,
            session.attr,
        )
        if actual != transaction["target_value"]:
            raise context.error_factory(
                f"Info metadata commit readback mismatch for {session.root}.{session.attr}"
            )
        context.call_adapter("undo_info", closeChunk=True)
        transaction["chunk_open"] = False
        context.set_active_transaction(None)
        return transaction["target_value"] != transaction["original_value"]

    def rollback(self, model_root: str, session: InfoMetadataSession) -> None:
        """Close, undo, clear, and verify the exact captured preimage."""
        context = self._context
        transaction = self._active_session(model_root, session)
        if transaction["chunk_open"]:
            context.call_adapter("undo_info", closeChunk=True)
            transaction["chunk_open"] = False
        if transaction["mutated"]:
            context.call_adapter("undo")
            transaction["mutated"] = False
        context.set_active_transaction(None)
        actual = self._string_value(
            context.call_adapter("get_attr", f"{session.root}.{session.attr}"),
            session.root,
            session.attr,
        )
        if actual != transaction["original_value"]:
            error = context.error_factory(
                f"Info metadata rollback preimage mismatch for {session.root}.{session.attr}"
            )
            error.rollback_pending = False
            raise error

    def _active_session(
        self, model_root: str, session: InfoMetadataSession
    ) -> MutableMapping[str, Any]:
        context = self._context
        if not isinstance(session, InfoMetadataSession):
            raise context.error_factory("invalid Info metadata session")
        transaction = context.active_transaction(model_root)
        if (
            transaction.get("kind") != self._KIND
            or transaction.get("token") is not session.token
            or transaction.get("root") != session.root
            or transaction.get("attr") != session.attr
        ):
            raise context.error_factory("Info metadata session identity mismatch")
        return transaction

    def _string_value(self, value: Any, root: str, attr: str) -> str:
        if not isinstance(value, str):
            raise self._context.error_factory(
                f"Info metadata must be a string for {root}.{attr}"
            )
        return value


__all__ = [
    "InfoMetadataSession",
    "MayaInfoMetadataTransaction",
    "MayaInfoMetadataTransactionContext",
]
