"""Path-hit-tested Body picker for the Animator Toolset.

The Illustrator-authored SVG supplies the visible controller shapes and exact
click regions.  A high-DPI PNG supplies the neutral character silhouette.
"""

from pathlib import Path

from ..qt_compat import Signal
from .svg_picker_widget import SvgPickerWidget, SvgRegionSource

_ASSET_DIR = Path(__file__).resolve().parents[1] / "assets" / "animator_toolset"

_BODY_REGIONS = [
    {"id": "head", "bone_name": "頭"},
    {"id": "neck", "bone_name": "首"},
    {"id": "upper_body", "bone_name": "上半身"},
    {"id": "upper_body_2", "bone_name": "上半身2"},
    {"id": "lower_body", "bone_name": "下半身"},
    {"id": "left_shoulder", "bone_name": "左肩"},
    {"id": "right_shoulder", "bone_name": "右肩"},
    {"id": "left_upper_arm", "bone_name": "左腕"},
    {"id": "right_upper_arm", "bone_name": "右腕"},
    {"id": "left_lower_arm", "bone_name": "左ひじ"},
    {"id": "right_lower_arm", "bone_name": "右ひじ"},
    {"id": "left_wrist", "bone_name": "左手首"},
    {"id": "right_wrist", "bone_name": "右手首"},
    {"id": "left_upper_leg", "bone_name": "左足"},
    {"id": "right_upper_leg", "bone_name": "右足"},
    {"id": "left_lower_leg", "bone_name": "左ひざ"},
    {"id": "right_lower_leg", "bone_name": "右ひざ"},
    {"id": "left_foot", "bone_name": "左足首"},
    {"id": "right_foot", "bone_name": "右足首"},
    {"id": "left_toe", "bone_name": "左つま先"},
    {"id": "right_toe", "bone_name": "右つま先"},
    {"id": "center", "bone_name": "センター"},
    {"id": "groove", "bone_name": "グルーブ"},
    {"id": "waist", "bone_name": "腰"},
    {"id": "both_eyes", "bone_name": "両目"},
    {"id": "left_eye", "bone_name": "左目"},
    {"id": "right_eye", "bone_name": "右目"},
    {"id": "left_shoulder_p", "bone_name": "左肩P"},
    {"id": "right_shoulder_p", "bone_name": "右肩P"},
    {"id": "left_toe_ik", "bone_name": "左つま先ＩＫ"},
    {"id": "right_toe_ik", "bone_name": "右つま先ＩＫ"},
    {"id": "left_ik", "bone_name": "左足ＩＫ"},
    {"id": "right_ik", "bone_name": "右足ＩＫ"},
    {"id": "master", "bone_name": "全ての親"},
]

_BODY_SOURCES = tuple(
    SvgRegionSource(element_id, region_id)
    for element_id, region_id in (
        ("head", "head"),
        ("neck", "neck"),
        # Illustrator's chest path is 上半身2; the smaller abdomen path is 上半身.
        ("upper_body", "upper_body_2"),
        ("upper_body_2", "upper_body"),
        ("lower_body", "lower_body"),
        ("left_shoulder", "left_shoulder"),
        ("right_shoulder", "right_shoulder"),
        ("left_upper_arm", "left_upper_arm"),
        ("right_upper_arm", "right_upper_arm"),
        ("left_lower_arm", "left_lower_arm"),
        ("right_lower_arm", "right_lower_arm"),
        ("left_wrist", "left_wrist"),
        ("right_wrist", "right_wrist"),
        ("left_upper_leg", "left_upper_leg"),
        ("left_upper_leg-2", "right_upper_leg"),
        ("left_lower_leg", "left_lower_leg"),
        ("left_lower_leg-2", "right_lower_leg"),
        ("left_foot", "left_foot"),
        ("left_foot-2", "right_foot"),
        ("left_toe", "left_toe"),
        ("left_toe-2", "right_toe"),
        ("center", "center"),
        ("groove", "groove"),
        ("waist", "waist"),
        ("both_eyes", "both_eyes"),
        ("left_eye", "left_eye"),
        ("right_eye", "right_eye"),
        ("left_shoulder_p", "left_shoulder_p"),
        ("right_shoulder_p", "right_shoulder_p"),
        ("left_toe_IK", "left_toe_ik"),
        ("right_toe_IK", "right_toe_ik"),
        ("left_ik", "left_ik"),
        ("right_ik", "right_ik"),
        ("mirror_sel-2", "ik_enable_left"),
        ("mirror_sel-3", "ik_enable_right"),
        ("master", "master"),
        ("some_function", "select_all"),
        ("some_function-2", "clear_selection"),
        ("reset_pose", "reset_pose"),
        ("mirror_sel", "mirror_sel"),
        ("fingers_left", "fingers_left"),
        ("fingers_right", "fingers_right"),
    )
)


class BodyPickerWidget(SvgPickerWidget):
    """Clickable Body picker canvas using the shipped 268×378 artwork."""

    region_clicked = Signal(str)
    regions_selected = Signal(object)
    mirror_selection_clicked = Signal()
    goto_finger_clicked = Signal()
    reset_pose_clicked = Signal()
    select_all_clicked = Signal()
    clear_selection_clicked = Signal()
    ik_toggled = Signal(str, bool)
    ik_enable_toggle_clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(
            _ASSET_DIR / "animpicker_body.svg",
            background_path=_ASSET_DIR / "animpicker_bg.png",
            region_sources=_BODY_SOURCES,
            region_labels={
                "select_all": "ALL",
                "clear_selection": "CLEAR",
                "reset_pose": "Reset Pose",
                "mirror_sel": "Mirror Sel",
            },
            tooltip_labels={
                **{region["id"]: region["bone_name"] for region in _BODY_REGIONS},
                "select_all": "現在のMMDモデルの全ボーンを選択",
                "clear_selection": "選択をクリア",
                "reset_pose": "選択中のボーンをバインドポーズへ戻す",
                "mirror_sel": "反対側のボーンを選択",
                "fingers_left": "指Pickerへ移動",
                "fingers_right": "指Pickerへ移動",
                "ik_enable_left": "左脚のIK Enableを切り替え",
                "ik_enable_right": "右脚のIK Enableを切り替え",
            },
            removed_element_ids={"bg.png", "alignment-guides"},
            parent=parent,
        )
        self.setObjectName("BodyPickerWidget")
        self.shape_clicked.connect(self._on_shape_clicked)
        self.shapes_selected.connect(self._on_shapes_selected)

    def _on_shapes_selected(self, region_ids: list[str]) -> None:
        selectable = [
            region_id
            for region_id in region_ids
            if region_id
            not in {
                "select_all",
                "clear_selection",
                "reset_pose",
                "mirror_sel",
                "fingers_left",
                "fingers_right",
                "ik_enable_left",
                "ik_enable_right",
            }
        ]
        self.regions_selected.emit(selectable)

    def _on_shape_clicked(self, region_id: str) -> None:
        if region_id == "select_all":
            self.select_all_clicked.emit()
        elif region_id == "clear_selection":
            self.clear_selection_clicked.emit()
        elif region_id == "mirror_sel":
            self.mirror_selection_clicked.emit()
        elif region_id in {"fingers_left", "fingers_right"}:
            self.goto_finger_clicked.emit()
        elif region_id == "reset_pose":
            self.reset_pose_clicked.emit()
        elif region_id in {"ik_enable_left", "ik_enable_right"}:
            self.ik_enable_toggle_clicked.emit(region_id.removeprefix("ik_enable_"))
        else:
            if region_id in {"left_ik", "right_ik"}:
                self.ik_toggled.emit(region_id.removesuffix("_ik"), True)
            self.region_clicked.emit(region_id)
