"""Tests for shared strict Maya metadata reads."""

from __future__ import annotations

from typing import Any

import pytest

from mmd_tools.adapters.maya_metadata_read_support import MayaMetadataReadSupport


class _ReadError(ValueError):
    pass


class _Cmds:
    def __init__(self) -> None:
        self.attrs: dict[tuple[str, str], Any] = {
            ("|root", "text"): "value",
            ("|root", "index"): 2,
            ("|root", "weight"): 0.5,
            ("|root", "vector"): [(1.0, 2.0, 3.0)],
        }

    def object_exists(self, node: str) -> bool:
        return node == "|root"

    def attribute_exists(self, attr: str, node: str) -> bool:
        return (node, attr) in self.attrs

    def get_attr(self, path: str) -> Any:
        node, attr = path.rsplit(".", 1)
        return self.attrs[(node, attr)]


def _support(cmds: _Cmds | None = None) -> MayaMetadataReadSupport:
    return MayaMetadataReadSupport(cmds or _Cmds(), error_factory=_ReadError)


def test_reads_strict_scalar_and_vector_values() -> None:
    support = _support()

    support.require_root("|root")
    assert support.required_string("|root", "text") == "value"
    assert support.required_int("|root", "index", minimum=0, maximum=2) == 2
    assert support.required_number("|root", "weight") == 0.5
    assert support.required_vector("|root", "vector") == (1.0, 2.0, 3.0)


@pytest.mark.parametrize(
    ("attr", "value", "reader", "message"),
    (
        ("text", 1, "required_string", "exact string"),
        ("index", True, "required_int", "integer"),
        ("weight", float("nan"), "required_number", "finite number"),
        ("vector", (1.0, 2.0), "required_vector", "vector3"),
    ),
)
def test_rejects_lossy_or_invalid_values(
    attr: str,
    value: Any,
    reader: str,
    message: str,
) -> None:
    cmds = _Cmds()
    cmds.attrs[("|root", attr)] = value

    with pytest.raises(_ReadError, match=message):
        getattr(_support(cmds), reader)("|root", attr)


def test_preserves_required_and_root_error_messages() -> None:
    support = _support()

    with pytest.raises(_ReadError, match=r"\|root\.missing is required"):
        support.required("|root", "missing")
    with pytest.raises(_ReadError, match="model root does not exist"):
        support.require_root("|missing")
