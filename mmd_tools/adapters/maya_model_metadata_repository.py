"""Read strict model-header metadata from an injected Maya adapter."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from mmd_tools.adapters.maya_metadata_read_support import MayaMetadataReadSupport
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
        read_support: MayaMetadataReadSupport,
    ) -> None:
        self._read = read_support

    def read_model_metadata(self, root: str) -> Mapping[str, Any]:
        """Return the canonical model-header mapping for an existing root."""
        self._read.require_root(root)
        return {
            "name": self._read.required_string(root, ATTR_MMD_MODEL_NAME),
            "name_english": self._read.required_string(root, ATTR_MMD_MODEL_NAME_EN),
            "comment": self._read.required_string(root, ATTR_MMD_COMMENT),
            "comment_english": self._read.required_string(root, ATTR_MMD_COMMENT_EN),
        }


__all__ = ["MayaModelMetadataRepository"]
