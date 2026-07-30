"""SelectionChanged and UUID-authority contracts for Animator pickers."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.ui.presenters.animation_presenter import AnimationPresenter  # noqa: E402


class _Picker:
    def __init__(self):
        self.selected_regions = None

    def set_selected_regions(self, regions):
        self.selected_regions = list(regions)


def _presenter(*, model_root=None, cmds=None):
    presenter = AnimationPresenter.__new__(AnimationPresenter)
    presenter.app_state = SimpleNamespace(current_model_root=model_root)
    presenter.maya_adapter = SimpleNamespace(_cmds=cmds) if cmds is not None else SimpleNamespace()
    presenter._bone_name_to_joint = {"頭": "|ns:model|Skeleton|head_jnt", "左親指0": "|ns:model|Skeleton|thumb_jnt"}
    presenter.view = SimpleNamespace(body_picker=_Picker(), finger_picker=_Picker())
    return presenter


def test_selection_sync_highlights_body_and_finger_and_clears():
    presenter = _presenter()
    presenter._set_picker_selection_from_nodes(["|ns:model|Skeleton|head_jnt", "|ns:model|Skeleton|thumb_jnt"])
    assert presenter.view.body_picker.selected_regions == ["head"]
    assert presenter.view.finger_picker.selected_regions == ["left_thumb_0"]

    presenter._set_picker_selection_from_nodes([])
    assert presenter.view.body_picker.selected_regions == []
    assert presenter.view.finger_picker.selected_regions == []


def test_control_owned_stale_or_ambiguous_uuid_fails_closed():
    cmds = MagicMock()
    presenter = _presenter(model_root="|ns:model|MMDModel", cmds=cmds)
    metadata = {
        "owner": "CONTROL_OWNED",
        "controls": {"center": "control-uuid"},
        "bindings": {"center": {"jointUuid": "joint-uuid"}},
    }
    with patch(
        "mmd_tools.core.mmd_control_rig_builder.read_mmd_control_rig_metadata",
        return_value=metadata,
    ), patch(
        "mmd_tools.core.mmd_control_rig_builder.resolve_mmd_control_rig_binding_joint",
        return_value="|ns:model|Skeleton|center_jnt",
    ):
        cmds.ls.return_value = []
        assert presenter._joint_for_rig_control("|ns:model|Controls|center") is None

        cmds.ls.return_value = ["uuid-a", "uuid-b"]
        assert presenter._joint_for_rig_control("|ns:model|Controls|center") is None


def test_control_owned_direct_joint_selection_uses_binding_uuid():
    cmds = MagicMock()
    presenter = _presenter(model_root="|ns:model|MMDModel", cmds=cmds)
    metadata = {
        "owner": "CONTROL_OWNED",
        "controls": {"center": "control-uuid"},
        "bindings": {"center": {"jointUuid": "joint-uuid"}},
    }

    def ls(value, *, uuid=False, long=False, **_kwargs):
        if uuid:
            return ["joint-uuid"]
        if long:
            return ["|ns:model|Skeleton|center_jnt"]
        return []

    cmds.ls.side_effect = ls
    with patch(
        "mmd_tools.core.mmd_control_rig_builder.read_mmd_control_rig_metadata",
        return_value=metadata,
    ), patch(
        "mmd_tools.core.mmd_control_rig_builder.resolve_mmd_control_rig_binding_joint",
        return_value="|ns:model|Skeleton|center_jnt",
    ):
        assert (
            presenter._joint_for_rig_control("|ns:model|Skeleton|center_jnt")
            == "|ns:model|Skeleton|center_jnt"
        )


def test_selection_changed_callback_is_read_only_and_removed_on_teardown():
    cmds = MagicMock()
    jobs = {}
    next_id = [1]

    def script_job(**kwargs):
        if "event" in kwargs:
            job_id = next_id[0]
            next_id[0] += 1
            jobs[job_id] = kwargs["event"]
            return job_id
        if "exists" in kwargs:
            return kwargs["exists"] in jobs
        jobs.pop(kwargs["kill"], None)

    cmds.scriptJob.side_effect = script_job
    presenter = _presenter(cmds=cmds)
    presenter._selection_sync_jobs = []
    presenter._disposed = False
    presenter._sync_picker_to_actual_selection = MagicMock()
    presenter._install_selection_sync_job()

    assert presenter._selection_sync_jobs == [1]
    with patch("mmd_tools.ui.qt_compat.QTimer.singleShot", side_effect=lambda _delay, callback: callback()):
        jobs[1][1]()
    presenter._sync_picker_to_actual_selection.assert_called_once_with()
    presenter._remove_selection_sync_jobs()
    assert jobs == {}
