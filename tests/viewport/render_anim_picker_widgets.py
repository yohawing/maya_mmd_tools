"""Render Animator Toolset picker widgets with Maya's real Qt runtime.

This is a visual smoke helper rather than a pixel-golden test.  It writes the
Body picker, Finger picker, and their containing Animation tab so layout and
asset-loading regressions can be inspected without opening the Maya GUI.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mmd_tools.ui.qt_compat import QApplication, QPixmap, QPointF, Qt  # noqa: E402
from mmd_tools.ui.tabs.animation_tab import AnimationTab  # noqa: E402
from mmd_tools.ui.presenters.animation_presenter import AnimationPresenter  # noqa: E402
from mmd_tools.core.display_frame_resolver import PickerGroup, PickerItem  # noqa: E402
from mmd_tools.core.morph_metadata_reader import MorphInfo, categorize_morphs  # noqa: E402
from mmd_tools.ui.widgets.body_picker_widget import BodyPickerWidget  # noqa: E402
from mmd_tools.ui.widgets.finger_picker_widget import FingerPickerWidget  # noqa: E402


def _render(widget, output_path: Path) -> None:
    widget.show()
    QApplication.processEvents()
    pixmap = QPixmap(widget.size())
    pixmap.fill(Qt.transparent)
    widget.render(pixmap)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not pixmap.save(str(output_path), "PNG"):
        raise RuntimeError(f"Failed to save picker render: {output_path}")
    widget.close()


class _PreviewAdapter:
    def __init__(self):
        self.values = {
            "faceBS.weight[0]": 0.72,
            "faceBS.weight[1]": 0.15,
            "mouthBS.weight[0]": 0.0,
        }

    def get_attr(self, plug):
        return self.values.get(plug, 0.0)

    def set_attr(self, plug, value):
        self.values[plug] = value


def _populate_preview_lists(tab: AnimationTab) -> None:
    presenter = object.__new__(AnimationPresenter)
    presenter.view = tab
    presenter.maya_adapter = _PreviewAdapter()
    presenter._morph_sliders = {}
    presenter._morph_targets = {
        "笑い": [("faceBS", 0)],
        "まばたき": [("faceBS", 1)],
        "あ": [("mouthBS", 0)],
        "涙": [("faceBS", 2)],
    }
    presenter._network_morph_targets = {}
    presenter._morph_indices = {"笑い": 19, "まばたき": 20, "あ": 24}
    presenter._morph_controller = None
    presenter._populate_morph_groups(
        categorize_morphs(
            [
                MorphInfo("笑い", "Smile", 2, "vertex", 19),
                MorphInfo("まばたき", "Blink", 2, "vertex", 20),
                MorphInfo("あ", "A", 3, "vertex", 24),
                MorphInfo("涙", "Tears", 4, "vertex", 30),
            ]
        )
    )
    presenter._populate_display_frame_tree(
        [
            PickerGroup(
                "表情",
                "Expressions",
                1,
                (
                    PickerItem(1, 19, "", "笑い"),
                    PickerItem(1, 20, "", "まばたき"),
                ),
            ),
            PickerGroup(
                "体（上）",
                "Upper Body",
                0,
                (
                    PickerItem(0, 3, "upper_body_jnt", "上半身"),
                    PickerItem(0, 4, "neck_jnt", "首"),
                ),
            ),
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()

    _app = QApplication.instance() or QApplication([])

    body_picker = BodyPickerWidget()
    finger_picker = FingerPickerWidget()
    body_picker.resize(420, 530)
    finger_picker.resize(420, 530)
    assert len(body_picker.region_ids) == 38
    assert len(finger_picker.region_ids) == 32
    canvas_rect = body_picker._canvas_rect()
    scaled_head_center = QPointF(
        canvas_rect.x() + canvas_rect.width() * 134.0 / 268.0,
        canvas_rect.y() + canvas_rect.height() * 30.0 / 378.0,
    )
    assert body_picker._region_at(scaled_head_center) == "head"
    selection_rect = canvas_rect.adjusted(1.0, 1.0, -1.0, -1.0)
    assert {"head", "left_upper_arm", "right_upper_arm"} <= set(
        body_picker._regions_in_rect(selection_rect)
    )
    body_picker.set_selected_regions({"head", "left_upper_arm", "right_upper_arm"})
    finger_picker.set_selected_regions({"left_palm", "left_index_1", "right_thumb_0"})
    _render(body_picker, args.out_dir / "body.png")
    _render(finger_picker, args.out_dir / "finger.png")

    body_tab = AnimationTab()
    body_tab.resize(420, 805)
    _render(body_tab, args.out_dir / "animation-tab-body.png")

    finger_tab = AnimationTab()
    finger_tab.resize(420, 805)
    finger_tab.picker_tabs.setCurrentIndex(finger_tab.TAB_FINGER)
    _render(finger_tab, args.out_dir / "animation-tab-finger.png")

    morph_tab = AnimationTab()
    morph_tab.resize(420, 805)
    _populate_preview_lists(morph_tab)
    morph_tab.picker_tabs.setCurrentIndex(morph_tab.TAB_MORPH)
    _render(morph_tab, args.out_dir / "animation-tab-morph.png")

    display_tab = AnimationTab()
    display_tab.resize(420, 805)
    _populate_preview_lists(display_tab)
    display_tab.picker_tabs.setCurrentIndex(display_tab.TAB_OTHER)
    _render(display_tab, args.out_dir / "animation-tab-display.png")

    visibility_tab = AnimationTab()
    visibility_tab.resize(420, 805)
    visibility_tab.visibility_toggle.setChecked(True)
    _render(visibility_tab, args.out_dir / "animation-tab-visibility.png")

    narrow_tab = AnimationTab()
    narrow_tab.resize(300, 700)
    _render(narrow_tab, args.out_dir / "animation-tab-body-narrow.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
