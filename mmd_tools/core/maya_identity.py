"""Maya node identity helpers for injected command adapters."""

from __future__ import annotations

from typing import Any


def canonical_node_identity(adapter: Any, node: Any) -> str | None:
    """Resolve one node alias to a unique Maya long identity."""
    if not isinstance(node, str) or not node.strip():
        return None
    if node.startswith("|"):
        return node
    try:
        matches = adapter.ls(node, long=True) or []
    except Exception:
        return None
    if isinstance(matches, (str, bytes, bytearray)) or len(matches) != 1:
        return None
    identity = matches[0]
    return identity if isinstance(identity, str) and identity else None


def same_node_identity(adapter: Any, left: Any, right: Any) -> bool:
    """Return whether two aliases uniquely resolve to the same Maya node."""
    if left == right and isinstance(left, str) and bool(left):
        return True
    left_identity = canonical_node_identity(adapter, left)
    right_identity = canonical_node_identity(adapter, right)
    return left_identity is not None and left_identity == right_identity


__all__ = ["canonical_node_identity", "same_node_identity"]
