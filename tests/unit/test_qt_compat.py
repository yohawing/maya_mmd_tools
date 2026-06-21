"""qt_compat のプロジェクト固有 UI 挙動を検証する。"""

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.ui.qt_compat import QComboBox, QDoubleSpinBox, QSlider, QSpinBox  # noqa: E402


class _WheelEvent:
    def __init__(self):
        self.ignored = False

    def ignore(self):
        self.ignored = True


def test_value_widgets_ignore_wheel_events():
    """スクロール中の accidental edit を避けるため、値ウィジェットは wheel を消費しない。"""
    for widget_class in (QComboBox, QDoubleSpinBox, QSlider, QSpinBox):
        event = _WheelEvent()

        widget_class.wheelEvent(None, event)

        assert event.ignored
