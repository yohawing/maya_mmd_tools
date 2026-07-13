"""Finger picker canvas widget for the Animator Toolset.

QPushButton-based hand layout picker with absolutely positioned regions.
Pure Qt — no Maya dependency.
"""

from ..qt_compat import QLabel, QPushButton, Signal, QWidget

_CANVAS_W = 420
_CANVAS_H = 300

_LEFT_HAND_X = 10
_RIGHT_HAND_X = 230
_JOINT_W = 22
_JOINT_H = 20
_JOINT_Y = (52, 75, 98)

_SIDE_COLORS = {
    "left": {
        "bg": "rgba(90, 120, 170, 0.3)",
        "border": "rgba(90, 120, 170, 0.5)",
        "hover": "rgba(90, 120, 170, 0.55)",
    },
    "right": {
        "bg": "rgba(170, 100, 90, 0.3)",
        "border": "rgba(170, 100, 90, 0.5)",
        "hover": "rgba(170, 100, 90, 0.55)",
    },
}

_FULLWIDTH_DIGITS = "０１２３"
_FINGER_NAMES = {
    "thumb": "親指",
    "index": "人指",
    "middle": "中指",
    "ring": "薬指",
    "pinky": "小指",
}
_SIDE_PREFIX = {"left": "左", "right": "右"}

_LEFT_FINGER_COLS = {
    "index": _LEFT_HAND_X + 52,
    "middle": _LEFT_HAND_X + 78,
    "ring": _LEFT_HAND_X + 104,
    "pinky": _LEFT_HAND_X + 130,
}
_RIGHT_FINGER_COLS = {
    "index": _RIGHT_HAND_X + 52,
    "middle": _RIGHT_HAND_X + 78,
    "ring": _RIGHT_HAND_X + 104,
    "pinky": _RIGHT_HAND_X + 130,
}
_LEFT_THUMB_X = (28, 18, 12)
_RIGHT_THUMB_X = (379, 389, 395)


def _fw(num: int) -> str:
    return _FULLWIDTH_DIGITS[num]


def _build_finger_regions() -> list[dict]:
    regions: list[dict] = []

    for side, origin_x, finger_cols, thumb_x in (
        ("left", _LEFT_HAND_X, _LEFT_FINGER_COLS, _LEFT_THUMB_X),
        ("right", _RIGHT_HAND_X, _RIGHT_FINGER_COLS, _RIGHT_THUMB_X),
    ):
        prefix = _SIDE_PREFIX[side]

        for finger, col_x in finger_cols.items():
            finger_label = _FINGER_NAMES[finger]
            for joint_idx, joint_y in enumerate(_JOINT_Y, start=1):
                regions.append(
                    {
                        "id": f"{side}_{finger}_{joint_idx}",
                        "label": f"{prefix}{finger_label}{joint_idx}",
                        "bone_name": f"{prefix}{finger_label}{_fw(joint_idx)}",
                        "x": col_x,
                        "y": joint_y,
                        "w": _JOINT_W,
                        "h": _JOINT_H,
                        "side": side,
                        "finger": finger,
                    }
                )

        for joint_idx, joint_y in enumerate(_JOINT_Y):
            regions.append(
                {
                    "id": f"{side}_thumb_{joint_idx}",
                    "label": f"{prefix}親指{joint_idx}",
                    "bone_name": f"{prefix}親指{_fw(joint_idx)}",
                    "x": thumb_x[joint_idx],
                    "y": joint_y,
                    "w": _JOINT_W,
                    "h": _JOINT_H,
                    "side": side,
                    "finger": "thumb",
                }
            )

        palm_x = origin_x + 25
        regions.append(
            {
                "id": f"{side}_palm",
                "label": f"{prefix}手首",
                "bone_name": f"{prefix}手首",
                "short": "Palm",
                "x": palm_x,
                "y": 205,
                "w": 105,
                "h": 48,
                "side": side,
                "finger": "palm",
            }
        )

    return regions


_FINGER_REGIONS = _build_finger_regions()


def _finger_region_style(side: str, radius: int = 3) -> str:
    colors = _SIDE_COLORS[side]
    return (
        "QPushButton {"
        f"background-color: {colors['bg']};"
        f"border: 1px solid {colors['border']};"
        f"border-radius: {radius}px;"
        "padding: 0;"
        "}"
        "QPushButton:hover {"
        f"background-color: {colors['hover']};"
        "}"
    )


def _control_region_style() -> str:
    return (
        "QPushButton {"
        "background-color: rgba(60, 60, 60, 0.9);"
        "border: 1px solid #555;"
        "border-radius: 3px;"
        "font-size: 9px;"
        "color: #d0d0d0;"
        "padding: 0;"
        "}"
        "QPushButton:hover {"
        "background-color: rgba(80, 80, 80, 0.95);"
        "}"
    )


def _nav_button_style() -> str:
    return (
        "QPushButton {"
        "background-color: rgba(45, 45, 45, 0.82);"
        "border: 1px solid #3c5c76;"
        "border-radius: 3px;"
        "font-size: 9px;"
        "font-weight: 600;"
        "color: #cfe0ee;"
        "padding: 4px 8px;"
        "}"
        "QPushButton:hover {"
        "background-color: rgba(58, 88, 120, 0.75);"
        "}"
    )


class FingerPickerWidget(QWidget):
    """Clickable finger-joint picker canvas (420×300 px)."""

    region_clicked = Signal(str)
    mirror_selection_clicked = Signal()
    goto_body_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("FingerPickerWidget")
        self.setMinimumSize(_CANVAS_W, _CANVAS_H)
        self.setMaximumSize(_CANVAS_W, _CANVAS_H)
        self.setStyleSheet(
            "FingerPickerWidget {"
            "background-color: #2b2b2b;"
            "border: 1px solid #232323;"
            "border-radius: 2px;"
            "}"
        )

        self._buttons_by_id: dict[str, QPushButton] = {}

        self._create_hand_labels()
        self._create_region_buttons()
        self._create_nav_buttons()

    def _create_hand_labels(self) -> None:
        label_style = (
            "color: #b0b0b0;"
            "font-size: 9px;"
            "font-weight: 600;"
            "background: transparent;"
        )
        for object_name, text, x in (
            ("FingerPicker_LeftHandLabel", "Left Hand (左手)", _LEFT_HAND_X),
            ("FingerPicker_RightHandLabel", "Right Hand (右手)", _RIGHT_HAND_X),
        ):
            label = QLabel(text, self)
            label.setObjectName(object_name)
            label.setGeometry(x, 28, 180, 16)
            label.setStyleSheet(label_style)

    def _create_region_buttons(self) -> None:
        for region in _FINGER_REGIONS:
            region_id = region["id"]
            button = QPushButton(self)
            button.setObjectName(f"FingerRegion_{region_id}")
            button.setGeometry(region["x"], region["y"], region["w"], region["h"])
            button.setToolTip(region["label"])

            if region["finger"] == "palm":
                button.setText(region.get("short", region["label"]))
                button.setStyleSheet(_control_region_style())
            else:
                button.setText("")
                button.setStyleSheet(_finger_region_style(region["side"]))

            button.clicked.connect(
                lambda _checked=False, rid=region_id: self.region_clicked.emit(rid)
            )
            self._buttons_by_id[region_id] = button

    def _create_nav_buttons(self) -> None:
        body_button = QPushButton("◂ Body", self)
        body_button.setObjectName("FingerPicker_GotoBody")
        body_button.setGeometry(6, 6, 72, 22)
        body_button.setToolTip("Go back to Body picker")
        body_button.setStyleSheet(_nav_button_style())
        body_button.clicked.connect(self.goto_body_clicked.emit)
        self._buttons_by_id["goto_body"] = body_button

        mirror_button = QPushButton("Mirror Sel", self)
        mirror_button.setObjectName("FingerPicker_MirrorSel")
        mirror_button.setGeometry(326, 6, 88, 22)
        mirror_button.setToolTip("Mirror the current selection to the opposite side")
        mirror_button.setStyleSheet(_nav_button_style())
        mirror_button.clicked.connect(self.mirror_selection_clicked.emit)
        self._buttons_by_id["mirror_sel"] = mirror_button