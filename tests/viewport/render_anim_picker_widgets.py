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

from mmd_tools.ui.qt_compat import QApplication, QPixmap, Qt  # noqa: E402
from mmd_tools.ui.tabs.animation_tab import AnimationTab  # noqa: E402
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()

    _app = QApplication.instance() or QApplication([])

    body_picker = BodyPickerWidget()
    finger_picker = FingerPickerWidget()
    assert len(body_picker.region_ids) == 38
    assert len(finger_picker.region_ids) == 32
    _render(body_picker, args.out_dir / "body.png")
    _render(finger_picker, args.out_dir / "finger.png")

    body_tab = AnimationTab()
    body_tab.resize(420, 700)
    _render(body_tab, args.out_dir / "animation-tab-body.png")

    finger_tab = AnimationTab()
    finger_tab.resize(420, 700)
    finger_tab.picker_tabs.setCurrentIndex(finger_tab.TAB_FINGER)
    _render(finger_tab, args.out_dir / "animation-tab-finger.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
