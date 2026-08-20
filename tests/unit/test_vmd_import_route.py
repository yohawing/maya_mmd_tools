"""Pure route selection tests for VMD model imports."""

from __future__ import annotations

import pytest

from mmd_tools.converters.vmd_import_route import (
    VmdImportRouteError,
    plan_vmd_import_route,
)


def _plan(**overrides):
    values = {
        "scene_animation_only": False,
        "target_model": "|model",
        "bake_mode": False,
        "create_mmd_control_rig": False,
        "runtime_bake_available": False,
        "registered_sparse_available": False,
    }
    values.update(overrides)
    return plan_vmd_import_route(**values)


def test_scene_animation_route_does_not_require_model() -> None:
    plan = _plan(scene_animation_only=True, target_model=None)

    assert plan.route == "scene_animation_only"
    assert not plan.use_runtime_bake
    assert not plan.use_registered_sparse


@pytest.mark.parametrize("runtime", [False, True])
def test_control_rig_owns_model_route_and_can_use_registered_sparse(runtime: bool) -> None:
    plan = _plan(
        create_mmd_control_rig=True,
        runtime_bake_available=runtime,
        registered_sparse_available=True,
    )

    assert plan.route == "control_rig"
    assert not plan.use_runtime_bake
    assert plan.use_registered_sparse


def test_runtime_bake_wins_over_legacy_sparse_route() -> None:
    plan = _plan(runtime_bake_available=True, registered_sparse_available=True)

    assert plan.route == "runtime_bake"
    assert plan.use_runtime_bake
    assert not plan.use_registered_sparse


def test_registered_sparse_is_selected_before_plain_legacy_route() -> None:
    plan = _plan(registered_sparse_available=True)

    assert plan.route == "registered_sparse"
    assert not plan.use_runtime_bake
    assert plan.use_registered_sparse


def test_missing_model_and_bake_control_conflict_fail_closed() -> None:
    with pytest.raises(VmdImportRouteError, match="explicit target model"):
        _plan(target_model="")
    with pytest.raises(VmdImportRouteError, match="cannot be combined"):
        _plan(bake_mode=True, create_mmd_control_rig=True)
