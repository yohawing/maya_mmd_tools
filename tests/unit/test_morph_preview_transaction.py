"""Focused contracts for event-spanning morph preview transactions."""

from unittest.mock import Mock, call
from types import SimpleNamespace

import pytest

from mmd_tools.adapters.maya_model_authoring_coordinator import (
    MayaModelAuthoringCoordinator,
    MayaModelAuthoringCoordinatorError,
)
from mmd_tools.adapters.maya_scene_metadata_backend import (
    MayaSceneMetadataBackend,
    MayaSceneMetadataError,
)
from tests.unit.test_maya_scene_metadata_backend import FakeCmds


def _backend():
    cmds = FakeCmds()
    cmds.nodes.update({"|controller", "|controller.inputWeight[0]", "|controller.inputWeight[1]"})
    cmds.attrs.update(
        {
            ("|controller", "inputWeight[0]"): 0.25,
            ("|controller", "inputWeight[1]"): 0.5,
        }
    )
    return MayaSceneMetadataBackend(cmds), cmds


def test_drag_session_fixes_targets_and_avoids_rediscovery_on_moves():
    backend, cmds = _backend()
    cmds.ls = Mock(wraps=cmds.ls)
    session = backend.begin_morph_preview(
        "|root", ("|controller.inputWeight[0]", "|controller.inputWeight[1]")
    )
    begin_ls_calls = cmds.ls.call_count

    backend.apply_morph_preview("|root", session, (0.6, 0.6))
    backend.apply_morph_preview("|root", session, (0.8, 0.8))

    assert cmds.ls.call_count == begin_ls_calls
    assert backend.commit_morph_preview("|root", session) == 2
    assert cmds.attrs[("|controller", "inputWeight[0]")] == 0.8
    assert cmds.attrs[("|controller", "inputWeight[1]")] == 0.8
    assert cmds.undo_chunk_open is False


def test_mid_batch_failure_rolls_back_all_preimages_exactly_once():
    backend, cmds = _backend()
    session = backend.begin_morph_preview(
        "|root", ("|controller.inputWeight[0]", "|controller.inputWeight[1]")
    )
    cmds.fail_set_path = "|controller.inputWeight[1]"

    with pytest.raises(MayaSceneMetadataError, match="injected set failure"):
        backend.apply_morph_preview("|root", session, (0.9, 0.9))
    backend.rollback_morph_preview("|root", session)

    assert cmds.undo_count == 1
    assert cmds.attrs[("|controller", "inputWeight[0]")] == 0.25
    assert cmds.attrs[("|controller", "inputWeight[1]")] == 0.5
    assert cmds.undo_chunk_open is False


def test_empty_session_rollback_closes_without_undo():
    backend, cmds = _backend()
    session = backend.begin_morph_preview("|root", ("|controller.inputWeight[0]",))

    backend.rollback_morph_preview("|root", session)

    assert cmds.undo_count == 0
    assert cmds.undo_chunk_open is False


def test_preflight_rejects_locked_target_before_opening_chunk():
    backend, cmds = _backend()
    cmds.locks[("|controller", "inputWeight[1]")] = True

    with pytest.raises(MayaSceneMetadataError, match="locked"):
        backend.begin_morph_preview(
            "|root", ("|controller.inputWeight[0]", "|controller.inputWeight[1]")
        )

    assert cmds.undo_chunk_open is False
    assert cmds.undo_count == 0
    assert cmds.write_history == []


def test_session_identity_rejects_retargeting():
    backend, cmds = _backend()
    session = backend.begin_morph_preview("|root", ("|controller.inputWeight[0]",))

    with pytest.raises(MayaSceneMetadataError, match="value count"):
        backend.apply_morph_preview("|root", session, (0.2, 0.3))

    backend.rollback_morph_preview("|root", session)
    assert cmds.undo_count == 0


def test_coordinator_update_failure_rolls_back_once_and_never_commits():
    backend = Mock()
    backend.apply_morph_preview.side_effect = RuntimeError("write failed")
    coordinator = object.__new__(MayaModelAuthoringCoordinator)
    coordinator._backend = backend
    session = SimpleNamespace(root="|root", targets=("|controller.inputWeight[0]",))

    with pytest.raises(MayaModelAuthoringCoordinatorError, match="write failed"):
        coordinator.update_morph_preview(session, 0.4)

    backend.rollback_morph_preview.assert_called_once_with("|root", session)
    backend.commit_morph_preview.assert_not_called()


def test_synchronous_reset_orders_begin_apply_commit_without_full_read():
    backend = Mock()
    session = SimpleNamespace(
        root="|root", targets=("|controller.inputWeight[0]",), token=object()
    )
    backend.begin_morph_preview.return_value = session
    backend.apply_morph_preview.return_value = 1
    coordinator = object.__new__(MayaModelAuthoringCoordinator)
    coordinator._backend = backend

    assert coordinator.reset_morph_preview(
        "|root", ("|controller.inputWeight[0]",)
    ) == 1

    assert backend.method_calls == [
        call.begin_morph_preview(
            "|root",
            ("|controller.inputWeight[0]",),
            chunk_name="Reset MMD Morph Preview",
        ),
        call.apply_morph_preview("|root", session, (0.0,)),
        call.commit_morph_preview("|root", session),
    ]
