"""Common Body/Finger action-bar signal and state contracts."""

from __future__ import annotations

from tests.unit.test_animation_presenter import (
    _FakeAdapter,
    _FakeAppState,
    _FakeButton,
    _FakeView,
)

from mmd_tools.ui.presenters.animation_presenter import AnimationPresenter


def _presenter_with_common_actions():
    view = _FakeView()
    view.common_action_buttons = {
        key: _FakeButton() for key in ("reset", "mirror")
    }
    adapter = _FakeAdapter(
        joints_by_index={0: "left_arm_jnt", 1: "right_arm_jnt"},
        bone_names={"left_arm_jnt": "左腕", "right_arm_jnt": "右腕"},
    )
    presenter = AnimationPresenter(
        view,
        _FakeAppState(model_root="test_model"),
        maya_adapter=adapter,
    )
    return presenter, view, adapter


def test_common_buttons_are_connected_to_completed_presenter_handlers():
    presenter, view, adapter = _presenter_with_common_actions()
    adapter.selected = ["left_arm_jnt"]
    adapter._transforms["left_arm_jnt"] = ([1.0, 2.0, 3.0], [10.0, 20.0, 30.0])
    adapter._transforms["right_arm_jnt"] = ([0.0, 0.0, 0.0], [0.0, 0.0, 0.0])

    view.common_action_buttons["mirror"].clicked.emit()

    assert adapter._transforms["right_arm_jnt"] == (
        [-1.0, 2.0, 3.0],
        [10.0, -20.0, -30.0],
    )
    assert "Mirrored" in view.status_label.text()


def test_common_rest_pose_state_refreshes_without_duplicate_button_instances():
    presenter, view, adapter = _presenter_with_common_actions()
    button = view.common_action_buttons["reset"]
    assert view.common_action_buttons["reset"] is button
    adapter.selected = ["left_arm_jnt"]
    adapter._transforms["left_arm_jnt"] = ([1.0, 2.0, 3.0], [10.0, 20.0, 30.0])

    button.clicked.emit()

    assert button.text == "Reset Pose"
    assert adapter._transforms["left_arm_jnt"][1] == [0.0, 0.0, 0.0]
