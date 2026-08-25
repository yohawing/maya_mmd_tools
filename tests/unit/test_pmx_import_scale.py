"""Pure validation tests for the persisted PMX import scale authority."""

from __future__ import annotations

import pytest

from mmd_tools.core.exceptions import MMDImportException
from mmd_tools.io.pmx_importer import _require_effective_import_scale


@pytest.mark.parametrize("value", [0.1, 1, 10.0])
def test_effective_import_scale_accepts_finite_positive_numbers(value) -> None:
    assert _require_effective_import_scale(value) == float(value)


@pytest.mark.parametrize("value", [0.0, -1.0, float("inf"), float("nan"), True, "1"])
def test_effective_import_scale_rejects_unsafe_values(value) -> None:
    with pytest.raises(MMDImportException, match="finite positive"):
        _require_effective_import_scale(value)
