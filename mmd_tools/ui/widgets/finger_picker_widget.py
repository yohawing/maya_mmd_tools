"""Path-hit-tested Finger picker for the Animator Toolset.

The Illustrator-authored SVG contains 16 shapes per hand: palm, three thumb
segments, and three segments for each of the other four fingers.
"""

from pathlib import Path

from ..qt_compat import Signal
from .svg_picker_widget import SvgPickerWidget

_ASSET_DIR = Path(__file__).resolve().parents[1] / "assets" / "animator_toolset"
_FULLWIDTH_DIGITS = "０１２３"
_FINGER_NAMES = {
    "thumb": "親指",
    "index": "人指",
    "middle": "中指",
    "ring": "薬指",
    "pinky": "小指",
}
_SIDE_PREFIX = {"left": "左", "right": "右"}


def _bone_name(side: str, finger: str, joint_index: int) -> str:
    return f"{_SIDE_PREFIX[side]}{_FINGER_NAMES[finger]}{_FULLWIDTH_DIGITS[joint_index]}"


def _region(region_id: str, bone_name: str) -> dict:
    return {"id": region_id, "bone_name": bone_name}


_FINGER_REGIONS = []
for _side in ("left", "right"):
    _FINGER_REGIONS.append(_region(f"{_side}_palm", f"{_SIDE_PREFIX[_side]}手首"))
    for _finger in ("thumb", "index", "middle", "ring", "pinky"):
        _first = 0 if _finger == "thumb" else 1
        for _joint_index in range(_first, _first + 3):
            _FINGER_REGIONS.append(
                _region(
                    f"{_side}_{_finger}_{_joint_index}",
                    _bone_name(_side, _finger, _joint_index),
                )
            )


def _hand_shape_order(side: str) -> tuple[str, ...]:
    """Match Illustrator's element order for one hand to semantic IDs."""

    return (
        f"{side}_palm",
        f"{side}_index_1",
        f"{side}_thumb_1",
        f"{side}_thumb_2",
        f"{side}_middle_1",
        f"{side}_index_2",
        f"{side}_index_3",
        f"{side}_middle_2",
        f"{side}_middle_3",
        f"{side}_ring_1",
        f"{side}_ring_2",
        f"{side}_ring_3",
        f"{side}_pinky_1",
        f"{side}_pinky_2",
        f"{side}_pinky_3",
        f"{side}_thumb_0",
    )


# The SVG is front-facing: the first authored group is screen-right, which is
# the character's left hand; the second group is the character's right hand.
_FINGER_SHAPE_REGION_IDS = (
    _hand_shape_order("left") + _hand_shape_order("right") + ("back_to_body",)
)


class FingerPickerWidget(SvgPickerWidget):
    """Clickable Finger picker canvas using the shipped 268×378 artwork."""

    region_clicked = Signal(str)
    regions_selected = Signal(object)
    mirror_selection_clicked = Signal()
    goto_body_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(
            _ASSET_DIR / "animpicker_finger.svg",
            ordered_region_ids=_FINGER_SHAPE_REGION_IDS,
            region_labels={"back_to_body": "‹  Body"},
            tooltip_labels={region["id"]: region["bone_name"] for region in _FINGER_REGIONS},
            parent=parent,
        )
        self.setObjectName("FingerPickerWidget")
        self.shape_clicked.connect(self._on_shape_clicked)
        self.shapes_selected.connect(self._on_shapes_selected)

    def _on_shape_clicked(self, region_id: str) -> None:
        """Route the embedded navigation button separately from finger bones."""

        if region_id == "back_to_body":
            self.goto_body_clicked.emit()
        else:
            self.region_clicked.emit(region_id)

    def _on_shapes_selected(self, region_ids: list[str]) -> None:
        """Exclude the embedded navigation button from marquee selection."""

        self.regions_selected.emit(
            [region_id for region_id in region_ids if region_id != "back_to_body"]
        )
