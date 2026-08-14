"""Read strict model-header metadata from an injected Maya adapter."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from mmd_tools.core.constants import (
    ATTR_MMD_COMMENT,
    ATTR_MMD_COMMENT_EN,
    ATTR_MMD_MODEL_NAME,
    ATTR_MMD_MODEL_NAME_EN,
)


class MayaModelMetadataRepository:
    """Read the schema-v1 Model aggregate without owning transactions."""

    def __init__(
        self,
        cmds_adapter: Any,
        *,
        error_factory: Callable[[str], Exception],
    ) -> None:
        self._cmds = cmds_adapter
        self._error = error_factory

    def read_model_metadata(self, root: str) -> Mapping[str, Any]:
        """Return the canonical model-header mapping for an existing root."""
        self._require_root(root)
        return {
            "name": self._required_string(root, ATTR_MMD_MODEL_NAME),
            "name_english": self._required_string(root, ATTR_MMD_MODEL_NAME_EN),
            "comment": self._required_string(root, ATTR_MMD_COMMENT),
            "comment_english": self._required_string(root, ATTR_MMD_COMMENT_EN),
        }

    def _required_string(self, node: str, attr: str) -> str:
        value = self._required(node, attr)
        if not isinstance(value, str):
            raise self._error(f"{node}.{attr} must be an exact string")
        return value

    def _required(self, node: str, attr: str) -> Any:
        if not self._has_attr(node, attr):
            raise self._error(f"{node}.{attr} is required")
        try:
            return self._cmds.get_attr(f"{node}.{attr}")
        except Exception as exc:
            raise self._error(f"failed to read {node}.{attr}: {exc}") from exc

    def _has_attr(self, node: str, attr: str) -> bool:
        try:
            return bool(self._cmds.attribute_exists(attr, node))
        except Exception as exc:
            raise self._error(f"failed to inspect {node}.{attr}: {exc}") from exc

    def _require_root(self, root: Any) -> None:
        if not isinstance(root, str) or not root.strip():
            raise self._error("root must be a non-empty string")
        try:
            exists = self._cmds.object_exists(root)
        except Exception as exc:
            raise self._error(f"failed to inspect root {root!r}: {exc}") from exc
        if not exists:
            raise self._error(f"model root does not exist: {root!r}")


__all__ = ["MayaModelMetadataRepository"]
