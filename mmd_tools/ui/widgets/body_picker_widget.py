"""Body picker canvas widget for the Animator Toolset.

QPushButton-based silhouette picker with absolutely positioned regions.
Pure Qt — no Maya dependency.
"""

from ..qt_compat import QPushButton, Signal, QWidget

_CANVAS_W = 268
_CANVAS_H = 378

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
    "center": {
        "bg": "rgba(100, 160, 100, 0.3)",
        "border": "rgba(100, 160, 100, 0.5)",
        "hover": "rgba(100, 160, 100, 0.55)",
    },
}

_BODY_REGIONS = [
    # --- body silhouette (invisible hit targets) ---
    {
        "id": "head",
        "label": "頭",
        "bone_name": "頭",
        "x": 112,
        "y": 8,
        "w": 44,
        "h": 44,
        "side": "center",
        "group": "body",
        "radius": 22,
    },
    {
        "id": "neck",
        "label": "首",
        "bone_name": "首",
        "x": 124,
        "y": 50,
        "w": 20,
        "h": 13,
        "side": "center",
        "group": "body",
    },
    {
        "id": "upper_body",
        "label": "上半身",
        "bone_name": "上半身",
        "x": 94,
        "y": 60,
        "w": 80,
        "h": 58,
        "side": "center",
        "group": "body",
        "radius": 8,
    },
    {
        "id": "upper_body_2",
        "label": "上半身2",
        "bone_name": "上半身2",
        "x": 104,
        "y": 118,
        "w": 60,
        "h": 44,
        "side": "center",
        "group": "body",
    },
    {
        "id": "left_shoulder",
        "label": "左肩",
        "bone_name": "左肩",
        "x": 62,
        "y": 58,
        "w": 30,
        "h": 14,
        "side": "left",
        "group": "body",
    },
    {
        "id": "right_shoulder",
        "label": "右肩",
        "bone_name": "右肩",
        "x": 176,
        "y": 58,
        "w": 30,
        "h": 14,
        "side": "right",
        "group": "body",
    },
    {
        "id": "left_upper_arm",
        "label": "左上腕",
        "bone_name": "左腕",
        "x": 62,
        "y": 72,
        "w": 28,
        "h": 36,
        "side": "left",
        "group": "body",
    },
    {
        "id": "right_upper_arm",
        "label": "右上腕",
        "bone_name": "右腕",
        "x": 178,
        "y": 72,
        "w": 28,
        "h": 36,
        "side": "right",
        "group": "body",
    },
    {
        "id": "left_lower_arm",
        "label": "左ひじ",
        "bone_name": "左ひじ",
        "x": 56,
        "y": 124,
        "w": 24,
        "h": 34,
        "side": "left",
        "group": "body",
    },
    {
        "id": "right_lower_arm",
        "label": "右ひじ",
        "bone_name": "右ひじ",
        "x": 188,
        "y": 124,
        "w": 24,
        "h": 34,
        "side": "right",
        "group": "body",
    },
    {
        "id": "left_wrist",
        "label": "左手首",
        "bone_name": "左手首",
        "x": 50,
        "y": 178,
        "w": 26,
        "h": 14,
        "side": "left",
        "group": "body",
    },
    {
        "id": "right_wrist",
        "label": "右手首",
        "bone_name": "右手首",
        "x": 192,
        "y": 178,
        "w": 26,
        "h": 14,
        "side": "right",
        "group": "body",
    },
    {
        "id": "left_upper_leg",
        "label": "左足",
        "bone_name": "左足",
        "x": 100,
        "y": 206,
        "w": 30,
        "h": 72,
        "side": "left",
        "group": "body",
    },
    {
        "id": "right_upper_leg",
        "label": "右足",
        "bone_name": "右足",
        "x": 138,
        "y": 206,
        "w": 30,
        "h": 72,
        "side": "right",
        "group": "body",
    },
    {
        "id": "left_lower_leg",
        "label": "左ひざ",
        "bone_name": "左ひざ",
        "x": 102,
        "y": 280,
        "w": 26,
        "h": 66,
        "side": "left",
        "group": "body",
    },
    {
        "id": "right_lower_leg",
        "label": "右ひざ",
        "bone_name": "右ひざ",
        "x": 140,
        "y": 280,
        "w": 26,
        "h": 66,
        "side": "right",
        "group": "body",
    },
    {
        "id": "left_foot",
        "label": "左足首",
        "bone_name": "左足首",
        "x": 96,
        "y": 348,
        "w": 32,
        "h": 18,
        "side": "left",
        "group": "body",
    },
    {
        "id": "right_foot",
        "label": "右足首",
        "bone_name": "右足首",
        "x": 140,
        "y": 348,
        "w": 32,
        "h": 18,
        "side": "right",
        "group": "body",
    },
    {
        "id": "left_toe",
        "label": "左つま先",
        "bone_name": "左つま先",
        "x": 98,
        "y": 366,
        "w": 28,
        "h": 10,
        "side": "left",
        "group": "body",
    },
    {
        "id": "right_toe",
        "label": "右つま先",
        "bone_name": "右つま先",
        "x": 142,
        "y": 366,
        "w": 28,
        "h": 10,
        "side": "right",
        "group": "body",
    },
    # --- core controls ---
    {
        "id": "center",
        "label": "センター",
        "bone_name": "センター",
        "short": "Center",
        "x": 108,
        "y": 164,
        "w": 52,
        "h": 12,
        "side": "center",
        "group": "core",
    },
    {
        "id": "groove",
        "label": "グルーブ",
        "bone_name": "グルーブ",
        "short": "Groove",
        "x": 108,
        "y": 178,
        "w": 52,
        "h": 12,
        "side": "center",
        "group": "core",
    },
    {
        "id": "waist",
        "label": "腰",
        "bone_name": "腰",
        "short": "Waist",
        "x": 108,
        "y": 192,
        "w": 52,
        "h": 12,
        "side": "center",
        "group": "core",
    },
    # --- eye controls ---
    {
        "id": "both_eyes",
        "label": "両目",
        "bone_name": "両目",
        "short": "Eyes",
        "x": 200,
        "y": 12,
        "w": 50,
        "h": 12,
        "side": "center",
        "group": "eye",
    },
    {
        "id": "left_eye",
        "label": "左目",
        "bone_name": "左目",
        "short": "L Eye",
        "x": 200,
        "y": 27,
        "w": 50,
        "h": 12,
        "side": "left",
        "group": "eye",
    },
    {
        "id": "right_eye",
        "label": "右目",
        "bone_name": "右目",
        "short": "R Eye",
        "x": 200,
        "y": 42,
        "w": 50,
        "h": 12,
        "side": "right",
        "group": "eye",
    },
    # --- shoulder controls ---
    {
        "id": "left_shoulder_p",
        "label": "左肩P",
        "bone_name": "左肩P",
        "short": "L肩P",
        "x": 58,
        "y": 58,
        "w": 36,
        "h": 13,
        "side": "left",
        "group": "shoulder",
    },
    {
        "id": "right_shoulder_p",
        "label": "右肩P",
        "bone_name": "右肩P",
        "short": "R肩P",
        "x": 174,
        "y": 58,
        "w": 36,
        "h": 13,
        "side": "right",
        "group": "shoulder",
    },
    # --- extra controls ---
    {
        "id": "left_toe_ex",
        "label": "左足先EX",
        "bone_name": "左足先EX",
        "short": "L足先EX",
        "x": 62,
        "y": 367,
        "w": 42,
        "h": 11,
        "side": "left",
        "group": "extra",
    },
    {
        "id": "master",
        "label": "全ての親",
        "bone_name": "全ての親",
        "short": "全ての親",
        "x": 108,
        "y": 367,
        "w": 52,
        "h": 11,
        "side": "center",
        "group": "extra",
    },
    {
        "id": "right_toe_ex",
        "label": "右足先EX",
        "bone_name": "右足先EX",
        "short": "R足先EX",
        "x": 164,
        "y": 367,
        "w": 42,
        "h": 11,
        "side": "right",
        "group": "extra",
    },
    # --- IK/FK toggles ---
    {
        "id": "left_ik",
        "label": "左足ＩＫ",
        "bone_name": "左足ＩＫ",
        "x": 62,
        "y": 348,
        "w": 26,
        "h": 17,
        "side": "left",
        "group": "ik",
    },
    {
        "id": "right_ik",
        "label": "右足ＩＫ",
        "bone_name": "右足ＩＫ",
        "x": 180,
        "y": 348,
        "w": 26,
        "h": 17,
        "side": "right",
        "group": "ik",
    },
]

_LEG_SEGMENTS_BY_SIDE = {
    "left": ("left_upper_leg", "left_lower_leg"),
    "right": ("right_upper_leg", "right_lower_leg"),
}

_IK_REGION_BY_SIDE = {
    "left": "left_ik",
    "right": "right_ik",
}


def _body_region_style(side: str, radius: int = 3) -> str:
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


def _ik_region_style(is_ik: bool) -> str:
    if is_ik:
        return (
            "QPushButton {"
            "background-color: rgba(63, 143, 134, 0.95);"
            "border: 1px solid #4a8ab5;"
            "border-radius: 3px;"
            "font-size: 9px;"
            "font-weight: bold;"
            "color: #ffffff;"
            "padding: 0;"
            "}"
            "QPushButton:hover {"
            "background-color: rgba(79, 176, 164, 0.95);"
            "}"
        )
    return (
        "QPushButton {"
        "background-color: rgba(60, 60, 60, 0.75);"
        "border: 1px solid #555;"
        "border-radius: 3px;"
        "font-size: 9px;"
        "font-weight: bold;"
        "color: #a0a0a0;"
        "padding: 0;"
        "}"
        "QPushButton:hover {"
        "background-color: rgba(80, 80, 80, 0.85);"
        "}"
    )


class BodyPickerWidget(QWidget):
    """Clickable body-part picker canvas (268×378 px)."""

    region_clicked = Signal(str)
    mirror_selection_clicked = Signal()
    goto_finger_clicked = Signal()
    ik_toggled = Signal(str, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("BodyPickerWidget")
        self.setMinimumSize(_CANVAS_W, _CANVAS_H)
        self.setMaximumSize(_CANVAS_W, _CANVAS_H)
        self.setStyleSheet(
            "BodyPickerWidget {"
            "background-color: #2b2b2b;"
            "border: 1px solid #232323;"
            "border-radius: 2px;"
            "}"
        )

        self._buttons_by_id: dict[str, QPushButton] = {}
        self._ik_state: dict[str, bool] = {"left": True, "right": True}

        self._create_region_buttons()
        self._create_mirror_button()
        self._create_finger_buttons()
        self._update_leg_visibility()

    def _create_region_buttons(self) -> None:
        for region in _BODY_REGIONS:
            region_id = region["id"]
            button = QPushButton(self)
            button.setObjectName(f"BodyRegion_{region_id}")
            button.setGeometry(region["x"], region["y"], region["w"], region["h"])
            button.setToolTip(region["label"])

            group = region["group"]
            if group == "body":
                button.setText("")
                radius = region.get("radius", 3)
                button.setStyleSheet(_body_region_style(region["side"], radius))
                button.clicked.connect(
                    lambda _checked=False, rid=region_id: self.region_clicked.emit(rid)
                )
            elif group == "ik":
                side = "left" if region_id == "left_ik" else "right"
                button.clicked.connect(
                    lambda _checked=False, s=side: self._on_ik_clicked(s)
                )
                self._apply_ik_button_style(button, side)
            else:
                button.setText(region.get("short", region["label"]))
                button.setStyleSheet(_control_region_style())
                button.clicked.connect(
                    lambda _checked=False, rid=region_id: self.region_clicked.emit(rid)
                )

            self._buttons_by_id[region_id] = button

    def _create_mirror_button(self) -> None:
        button = QPushButton("Mirror Sel", self)
        button.setObjectName("BodyPicker_MirrorSel")
        button.setGeometry(6, 6, 88, 22)
        button.setToolTip("Mirror the current selection to the opposite side")
        button.setStyleSheet(
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
        button.clicked.connect(self.mirror_selection_clicked.emit)
        self._buttons_by_id["mirror_sel"] = button

    def _create_finger_buttons(self) -> None:
        finger_style = (
            "QPushButton {"
            "background-color: rgba(58, 88, 120, 0.55);"
            "border: 1px solid #3c5c76;"
            "border-radius: 3px;"
            "font-size: 8.5px;"
            "font-weight: 600;"
            "color: #cfe0ee;"
            "padding: 0;"
            "}"
            "QPushButton:hover {"
            "background-color: rgba(74, 110, 150, 0.7);"
            "}"
        )
        for finger_id, x in (("fingers_left", 20), ("fingers_right", 192)):
            button = QPushButton("Fingers ›", self)
            button.setObjectName(f"BodyPicker_{finger_id}")
            button.setGeometry(x, 216, 56, 17)
            button.setToolTip("Go to Finger picker")
            button.setStyleSheet(finger_style)
            button.clicked.connect(self.goto_finger_clicked.emit)
            self._buttons_by_id[finger_id] = button

    def _apply_ik_button_style(self, button: QPushButton, side: str) -> None:
        is_ik = self._ik_state[side]
        button.setText("IK" if is_ik else "FK")
        button.setStyleSheet(_ik_region_style(is_ik))

    def _on_ik_clicked(self, side: str) -> None:
        is_ik = not self._ik_state[side]
        self._ik_state[side] = is_ik

        ik_button = self._buttons_by_id[_IK_REGION_BY_SIDE[side]]
        self._apply_ik_button_style(ik_button, side)
        self._update_leg_visibility()

        self.ik_toggled.emit(side, is_ik)
        self.region_clicked.emit(_IK_REGION_BY_SIDE[side])

    def _update_leg_visibility(self) -> None:
        for side, segment_ids in _LEG_SEGMENTS_BY_SIDE.items():
            visible = not self._ik_state[side]
            for segment_id in segment_ids:
                button = self._buttons_by_id.get(segment_id)
                if button is not None:
                    button.setVisible(visible)