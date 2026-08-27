from __future__ import annotations

import copy

import pytest

from tools.gates.model_authoring_gate import (
    ModelAuthoringGateError,
    REQUIRED_OPERATIONS,
    _require_completed_worker_result,
)


def _result() -> dict:
    operations = [{"name": name, "status": "pass"} for name in REQUIRED_OPERATIONS]
    by_name = {item["name"]: item for item in operations}
    by_name["morph.create"]["created_types"] = [
        "bone",
        "vertex",
        "group",
        "material",
        "uv",
        "additional_uv1",
    ]
    by_name["morph.edit"]["edited_types"] = ["vertex", "bone", "group", "material"]
    by_name["morph.edit"]["roundtrip_types"] = ["uv", "additional_uv1"]
    before = {"model": {}, "materials": [], "bones": [], "morphs": [], "fingerprint": "same"}
    return {
        "operations": operations,
        "before": before,
        "after": copy.deepcopy(before),
        "negative_cases": [
            {"name": "writer_not_called", "status": "pass"},
            {"name": "unsupported_flip_impulse_reject", "status": "pass"},
        ],
    }


def test_completed_worker_result_requires_explicit_morph_coverage() -> None:
    operations, matrix, negative = _require_completed_worker_result(_result())
    assert all(item["status"] == "pass" for item in operations)
    assert all(item["status"] == "pass" for item in matrix.values())
    assert {item["name"] for item in negative} == {
        "writer_not_called",
        "unsupported_flip_impulse_reject",
    }


@pytest.mark.parametrize("detail", ["created_types", "edited_types", "roundtrip_types"])
def test_completed_worker_result_rejects_missing_morph_detail(detail: str) -> None:
    result = _result()
    by_name = {item["name"]: item for item in result["operations"]}
    if detail == "created_types":
        del by_name["morph.create"][detail]
    elif detail == "edited_types":
        del by_name["morph.edit"][detail]
    else:
        del by_name["morph.edit"][detail]
    with pytest.raises(ModelAuthoringGateError):
        _require_completed_worker_result(result)
