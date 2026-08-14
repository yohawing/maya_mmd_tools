"""Tests for the read-only Maya Model metadata repository."""

from __future__ import annotations

from typing import Any

import pytest

from mmd_tools.adapters.maya_model_metadata_repository import (
    MayaModelMetadataRepository,
)


class _RepositoryError(ValueError):
    pass


class _Cmds:
    def __init__(self) -> None:
        self.exists = True
        self.attrs: dict[tuple[str, str], Any] = {
            ("|root", "mmd_model_name"): "モデル",
            ("|root", "mmd_model_name_en"): "Model",
            ("|root", "mmd_comment"): "コメント",
            ("|root", "mmd_comment_en"): "Comment",
        }

    def object_exists(self, node: str) -> bool:
        return self.exists and node == "|root"

    def attribute_exists(self, attr: str, node: str) -> bool:
        return (node, attr) in self.attrs

    def get_attr(self, path: str) -> Any:
        node, attr = path.rsplit(".", 1)
        return self.attrs[(node, attr)]


def _repository(cmds: _Cmds) -> MayaModelMetadataRepository:
    return MayaModelMetadataRepository(cmds, error_factory=_RepositoryError)


def test_reads_exact_schema_v1_model_header_fields() -> None:
    repository = _repository(_Cmds())

    assert repository.read_model_metadata("|root") == {
        "name": "モデル",
        "name_english": "Model",
        "comment": "コメント",
        "comment_english": "Comment",
    }


def test_rejects_missing_root_and_required_field_with_injected_error() -> None:
    cmds = _Cmds()
    repository = _repository(cmds)

    with pytest.raises(_RepositoryError, match="model root does not exist"):
        repository.read_model_metadata("|missing")

    del cmds.attrs[("|root", "mmd_comment_en")]
    with pytest.raises(_RepositoryError, match=r"\|root\.mmd_comment_en is required"):
        repository.read_model_metadata("|root")


def test_rejects_non_string_semantic_value_without_coercion() -> None:
    cmds = _Cmds()
    cmds.attrs[("|root", "mmd_model_name")] = 1

    with pytest.raises(_RepositoryError, match="must be an exact string"):
        _repository(cmds).read_model_metadata("|root")
