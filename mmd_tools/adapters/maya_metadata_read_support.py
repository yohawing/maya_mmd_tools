"""Shared strict scalar reads for Maya metadata repositories."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from typing import Any


class MayaMetadataReadSupport:
    """Preserve the facade's fail-closed root and attribute semantics."""

    def __init__(
        self,
        cmds_adapter: Any,
        *,
        error_factory: Callable[[str], Exception],
    ) -> None:
        self._cmds = cmds_adapter
        self._error = error_factory

    def required(self, node: str, attr: str) -> Any:
        if not self.has_attr(node, attr):
            raise self._error(f"{node}.{attr} is required")
        try:
            return self._cmds.get_attr(f"{node}.{attr}")
        except Exception as exc:
            raise self._error(f"failed to read {node}.{attr}: {exc}") from exc

    def required_string(self, node: str, attr: str) -> str:
        value = self.required(node, attr)
        if not isinstance(value, str):
            raise self._error(f"{node}.{attr} must be an exact string")
        return value

    def required_int(
        self,
        node: str,
        attr: str,
        *,
        minimum: int | None = None,
        maximum: int | None = None,
    ) -> int:
        value = self.required(node, attr)
        if isinstance(value, bool) or not isinstance(value, int):
            raise self._error(f"{node}.{attr} must be an integer")
        if minimum is not None and value < minimum:
            raise self._error(f"{node}.{attr} must be >= {minimum}")
        if maximum is not None and value > maximum:
            raise self._error(f"{node}.{attr} must be <= {maximum}")
        return value

    def required_number(self, node: str, attr: str) -> float:
        value = self.required(node, attr)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise self._error(f"{node}.{attr} must be a finite number")
        return float(value)

    def required_vector(
        self, node: str, attr: str
    ) -> tuple[float, float, float]:
        value = self.required(node, attr)
        if (
            isinstance(value, (list, tuple))
            and len(value) == 1
            and isinstance(value[0], (list, tuple))
        ):
            value = value[0]
        if (
            isinstance(value, (str, bytes, bytearray))
            or not isinstance(value, Sequence)
            or len(value) != 3
        ):
            raise self._error(f"{node}.{attr} must be a vector3")
        numbers = []
        for item in value:
            if (
                isinstance(item, bool)
                or not isinstance(item, (int, float))
                or not math.isfinite(item)
            ):
                raise self._error(
                    f"{node}.{attr} must contain finite numeric vector3 values"
                )
            numbers.append(float(item))
        return tuple(numbers)  # type: ignore[return-value]

    def has_attr(self, node: str, attr: str) -> bool:
        try:
            return bool(self._cmds.attribute_exists(attr, node))
        except Exception as exc:
            raise self._error(f"failed to inspect {node}.{attr}: {exc}") from exc

    def canonical_identity(self, node: Any) -> str:
        if not isinstance(node, str) or not node:
            raise self._error(
                f"material binding identity must be a non-empty string: {node!r}"
            )
        if node.startswith("|"):
            return node
        try:
            long_names = self._cmds.ls(node, long=True) or []
        except Exception as exc:
            raise self._error(
                f"failed to canonicalize material node {node!r}: {exc}"
            ) from exc
        if len(long_names) == 1 and isinstance(long_names[0], str):
            return long_names[0]
        return node

    def call_adapter(self, method: str, *args: Any, **kwargs: Any) -> Any:
        try:
            return getattr(self._cmds, method)(*args, **kwargs)
        except AttributeError as exc:
            raise self._error(f"injected adapter is missing {method}()") from exc
        except Exception as exc:
            raise self._error(f"adapter {method}() failed: {exc}") from exc

    def require_root(self, root: Any) -> None:
        if not isinstance(root, str) or not root.strip():
            raise self._error("root must be a non-empty string")
        try:
            exists = self._cmds.object_exists(root)
        except Exception as exc:
            raise self._error(f"failed to inspect root {root!r}: {exc}") from exc
        if not exists:
            raise self._error(f"model root does not exist: {root!r}")


__all__ = ["MayaMetadataReadSupport"]
