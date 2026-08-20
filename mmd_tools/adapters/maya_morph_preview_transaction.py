"""Narrow event-spanning transaction authority for Morph preview weights."""

from __future__ import annotations

import math
from collections.abc import Callable, MutableMapping, Sequence
from dataclasses import dataclass
from typing import Any

from mmd_tools.adapters.native_authoring_command import (
    NativeAuthoringCommandError,
    NativeCommandProtocolError,
    NativeCommandUnavailable,
)


ErrorFactory = Callable[[str], Exception]
CanonicalIdentity = Callable[[Any], str]
RequireRoot = Callable[[Any], None]
CallAdapter = Callable[..., Any]
GetActiveTransaction = Callable[[], MutableMapping[str, Any] | None]
SetActiveTransaction = Callable[[MutableMapping[str, Any] | None], None]
ActiveTransaction = Callable[[str], MutableMapping[str, Any]]
UseNativeWeights = Callable[[], bool]
HasNativeCommandSurface = Callable[[], bool]


@dataclass(frozen=True)
class MorphPreviewSession:
    """Opaque event-spanning preview identity with a fixed write-set."""

    root: str
    targets: tuple[str, ...]
    token: object


@dataclass(frozen=True)
class MayaMorphPreviewTransactionContext:
    """Callbacks supplied by the metadata backend facade."""

    error_factory: ErrorFactory
    canonical_identity: CanonicalIdentity
    require_root: RequireRoot
    call_adapter: CallAdapter
    native_authoring: Any
    use_native_weights: UseNativeWeights
    has_native_command_surface: HasNativeCommandSurface
    get_active_transaction: GetActiveTransaction
    set_active_transaction: SetActiveTransaction
    active_transaction: ActiveTransaction


class MayaMorphPreviewTransaction:
    """Own one fixed-target preview chunk without reading other metadata."""

    _KIND = "morph_preview"

    def __init__(self, context: MayaMorphPreviewTransactionContext) -> None:
        self._context = context

    def begin(
        self,
        model_root: str,
        target_plugs: Sequence[str],
        *,
        chunk_name: str = "MMD Morph Preview",
    ) -> MorphPreviewSession:
        """Capture a fixed preview write-set and open one Maya undo chunk."""
        context = self._context
        if context.get_active_transaction() is not None:
            raise context.error_factory("a metadata write transaction is already active")
        root = context.canonical_identity(model_root)
        context.require_root(root)
        if not bool(context.call_adapter("undo_info", query=True, state=True)):
            raise context.error_factory("Maya undo must be enabled for morph preview")
        canonical = tuple(self._canonical_preview_plug(plug) for plug in target_plugs)
        if not canonical:
            raise context.error_factory("morph preview requires at least one target plug")
        if len(set(canonical)) != len(canonical):
            raise context.error_factory("morph preview target plugs must be unique")
        original: dict[str, float] = {}
        for plug in canonical:
            if not context.call_adapter("object_exists", plug):
                raise context.error_factory(
                    f"morph preview target does not exist: {plug!r}"
                )
            if bool(context.call_adapter("get_attr", plug, lock=True)):
                raise context.error_factory(f"morph preview target is locked: {plug!r}")
            original[plug] = self._preview_weight(
                context.call_adapter("get_attr", plug), plug
            )
        token = object()
        context.call_adapter("undo_info", openChunk=True, chunkName=chunk_name)
        context.set_active_transaction(
            {
                "root": root,
                "kind": "morph_preview",
                "token": token,
                "targets": canonical,
                "original_values": original,
                "target_values": dict(original),
                "chunk_open": True,
                "mutated": False,
            }
        )
        return MorphPreviewSession(root=root, targets=canonical, token=token)

    def apply(
        self,
        model_root: str,
        session: MorphPreviewSession,
        target_values: Sequence[float],
    ) -> int:
        """Write only the session's fixed targets and verify exact values."""
        context = self._context
        transaction = self._active_preview(model_root, session)
        if len(target_values) != len(session.targets):
            raise context.error_factory("morph preview update value count mismatch")
        expected = {
            plug: self._preview_weight(value, plug)
            for plug, value in zip(session.targets, target_values)
        }
        native_updates = [
            {"plug": plug, "value": value} for plug, value in expected.items()
        ]
        use_python = not context.use_native_weights()
        if context.use_native_weights():
            try:
                if not context.has_native_command_surface():
                    raise NativeCommandUnavailable(
                        "adapter has no native command surface"
                    )
                result = context.native_authoring.set_morph_weights(native_updates)
                canonical_values = result["values"]
                expected = {
                    plug: self._preview_weight(value, plug)
                    for plug, value in zip(session.targets, canonical_values)
                }
                transaction["mutated"] = True
            except NativeCommandUnavailable:
                use_python = True
            except NativeCommandProtocolError:
                # Maya returned from a registered command, but its envelope
                # cannot prove whether the no-op-looking command was queued.
                transaction["mutated"] = True
                raise
            except NativeAuthoringCommandError:
                # A transport/protocol failure can occur after Maya executed
                # the command. Preserve enough state for rollback.
                for plug, original in transaction["original_values"].items():
                    try:
                        actual = self._preview_weight(
                            context.call_adapter("get_attr", plug), plug
                        )
                    except Exception:
                        transaction["mutated"] = True
                        break
                    if not self._preview_weights_equal(actual, original):
                        transaction["mutated"] = True
                        break
                raise
        if use_python:
            for plug, value in expected.items():
                context.call_adapter("set_attr", plug, value)
                transaction["mutated"] = True
                actual = self._preview_weight(
                    context.call_adapter("get_attr", plug), plug
                )
                if not self._preview_weights_equal(actual, value):
                    raise context.error_factory(
                        f"morph preview readback mismatch for {plug!r}: "
                        f"expected {value!r}, got {actual!r}"
                    )
        transaction["target_values"] = expected
        return len(expected)

    def commit(self, model_root: str, session: MorphPreviewSession) -> int:
        """Close a preview chunk only after exact final-target readback."""
        context = self._context
        transaction = self._active_preview(model_root, session)
        for plug, expected in transaction["target_values"].items():
            actual = self._preview_weight(
                context.call_adapter("get_attr", plug), plug
            )
            if not self._preview_weights_equal(actual, expected):
                raise context.error_factory(
                    f"morph preview commit readback mismatch for {plug!r}"
                )
        context.call_adapter("undo_info", closeChunk=True)
        transaction["chunk_open"] = False
        context.set_active_transaction(None)
        return len(transaction["targets"])

    def rollback(self, model_root: str, session: MorphPreviewSession) -> None:
        """Close and undo one mutated chunk, then verify its preimage."""
        context = self._context
        transaction = self._active_preview(model_root, session)
        try:
            if transaction["chunk_open"]:
                context.call_adapter("undo_info", closeChunk=True)
                transaction["chunk_open"] = False
            if transaction["mutated"]:
                context.call_adapter("undo")
        finally:
            context.set_active_transaction(None)
        for plug, expected in transaction["original_values"].items():
            actual = self._preview_weight(
                context.call_adapter("get_attr", plug), plug
            )
            if not self._preview_weights_equal(actual, expected):
                raise context.error_factory(
                    f"morph preview rollback preimage mismatch for {plug!r}"
                )

    def _active_preview(
        self, model_root: str, session: MorphPreviewSession
    ) -> MutableMapping[str, Any]:
        context = self._context
        if not isinstance(session, MorphPreviewSession):
            raise context.error_factory("invalid morph preview session")
        transaction = context.active_transaction(model_root)
        if (
            transaction.get("kind") != self._KIND
            or transaction.get("token") is not session.token
            or transaction.get("root") != session.root
            or transaction.get("targets") != session.targets
        ):
            raise context.error_factory("morph preview session identity mismatch")
        return transaction

    def _canonical_preview_plug(self, plug: Any) -> str:
        context = self._context
        if not isinstance(plug, str) or "." not in plug:
            raise context.error_factory(f"invalid morph preview target plug: {plug!r}")
        node, attr = plug.rsplit(".", 1)
        names = context.call_adapter("ls", node, long=True) or ()
        if isinstance(names, (str, bytes, bytearray)) or len(names) != 1:
            raise context.error_factory(
                f"morph preview node has no unique canonical identity: {node!r}"
            )
        canonical = names[0]
        if not isinstance(canonical, str) or not canonical or not attr:
            raise context.error_factory(f"invalid morph preview target plug: {plug!r}")
        return f"{canonical}.{attr}"

    def _preview_weight(self, value: Any, plug: str) -> float:
        context = self._context
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise context.error_factory(
                f"morph preview weight must be numeric for {plug!r}"
            )
        result = float(value)
        if not math.isfinite(result):
            raise context.error_factory(
                f"morph preview weight must be finite for {plug!r}"
            )
        return result

    @staticmethod
    def _preview_weights_equal(actual: float, expected: float) -> bool:
        """Accept only the bounded round-trip error of Maya float attributes."""
        return math.isclose(actual, expected, rel_tol=1e-7, abs_tol=1e-7)


__all__ = [
    "MayaMorphPreviewTransaction",
    "MayaMorphPreviewTransactionContext",
    "MorphPreviewSession",
]
