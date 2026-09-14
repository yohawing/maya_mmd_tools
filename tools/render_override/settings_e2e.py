"""Verify the Renderer option box against an isolated Maya GUI."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.viewport.maya_e2e_harness import run_maya_e2e  # noqa: E402
from tools.render_override.common import require_requested_plugin  # noqa: E402

MARKER = "MMD RENDER SETTINGS FINISHED"


def run_probe(output, plugin):
    from maya import cmds, mel, OpenMayaUI
    from mmd_tools.converters.light_converter import create_mmd_light_controller
    from mmd_tools.ui import render_settings
    try:
        from PySide6 import QtWidgets
        from shiboken6 import wrapInstance
    except ImportError:
        from PySide2 import QtWidgets
        from shiboken2 import wrapInstance
    out = Path(output)
    report = {"status": "fail"}
    try:
        cmds.file(new=True, force=True)
        loaded = require_requested_plugin(cmds, Path(plugin), print)
        cmds.loadPlugin(str(ROOT / "plug-ins/mmd_tools_plugin.py"), quiet=True)
        report.update(mayaVersion=cmds.about(version=True), plugin=str(loaded),
                      pluginSha256=hashlib.sha256(loaded.read_bytes()).hexdigest())
        panels = {p: cmds.modelEditor(p, q=True, rendererOverrideName=True)
                  for p in cmds.getPanel(type="modelPanel")}
        light = create_mmd_light_controller()
        assert mel.eval('exists "mmdOrderedOptionBox"')
        panel = next(iter(panels))
        names = cmds.modelEditor(panel, q=True, rendererOverrideList=True)
        labels = cmds.modelEditor(panel, q=True, rendererOverrideListUI=True)
        assert labels[names.index("mmdOrdered")] == "MMD Render"
        renderer_menu = None
        for menu in cmds.lsUI(menus=True, long=True):
            command = cmds.menu(menu, q=True, postMenuCommand=True)
            if isinstance(command, str) and command.startswith("buildRendererMenu ") and command.endswith(" " + panel):
                renderer_menu = menu
                mel.eval(command)
                break
        assert renderer_menu, "Maya's Renderer menu was not found"
        items = cmds.menu(renderer_menu, q=True, itemArray=True)
        option = next(item for item in items if cmds.menuItem(item, q=True, optionBox=True)
                      and cmds.menuItem(item, q=True, command=True) == "mmdOrderedOptionBox")
        report["rendererMenu"] = renderer_menu
        report["optionBox"] = option
        mel.eval(cmds.menuItem(option, q=True, command=True))
        assert cmds.window(render_settings.WINDOW, exists=True)
        pointer = OpenMayaUI.MQtUtil.findControl("mmdRenderShadowMode")
        widget = wrapInstance(int(pointer), QtWidgets.QWidget)
        report["controlClass"] = widget.metaObject().className()
        combo = widget if isinstance(widget, QtWidgets.QComboBox) else widget.findChild(QtWidgets.QComboBox)
        assert combo is not None, report
        combo.setCurrentIndex(2)
        combo.activated.emit(2)
        assert cmds.getAttr(light + ".mmd_self_shadow_mode") == 2
        cmds.undo()
        assert cmds.getAttr(light + ".mmd_self_shadow_mode") == 1
        cmds.redo()
        assert cmds.getAttr(light + ".mmd_self_shadow_mode") == 2
        pointer = OpenMayaUI.MQtUtil.findControl("mmdRenderShadowDistance")
        distance_widget = wrapInstance(int(pointer), QtWidgets.QWidget)
        fields = distance_widget.findChildren(QtWidgets.QLineEdit)
        assert len(fields) == 1, [field.objectName() for field in fields]
        original_distance = cmds.getAttr(light + ".mmd_self_shadow_distance")
        fields[0].setText("2500")
        fields[0].editingFinished.emit()
        assert cmds.getAttr(light + ".mmd_self_shadow_distance") == 2500
        cmds.undo()
        assert cmds.getAttr(light + ".mmd_self_shadow_distance") == original_distance
        cmds.redo()
        assert cmds.getAttr(light + ".mmd_self_shadow_distance") == 2500
        cmds.deleteUI(render_settings.WINDOW)
        mel.eval("mmdOrderedOptionBox()")
        assert cmds.getAttr(light + ".mmd_self_shadow_mode") == 2
        assert cmds.getAttr(light + ".mmd_self_shadow_distance") == 2500
        assert panels == {p: cmds.modelEditor(p, q=True, rendererOverrideName=True) for p in panels}
        pointer = OpenMayaUI.MQtUtil.findWindow(render_settings.WINDOW)
        window = wrapInstance(int(pointer), QtWidgets.QWidget)
        window.grab().save(str(out / "settings.png"))
        cmds.file(rename=str(out / "settings.ma"))
        cmds.file(save=True, type="mayaAscii", force=True)
        cmds.file(str(out / "settings.ma"), open=True, force=True)
        mel.eval("mmdOrderedOptionBox()")
        assert cmds.getAttr(light + ".mmd_self_shadow_mode") == 2
        report["status"] = "pass"
    except Exception:
        report["error"] = traceback.format_exc()
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out / "probe.log").write_text(MARKER, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maya", default="2024")
    parser.add_argument("--port", type=int, default=7742)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    out = args.out_dir.resolve()
    plugin = ROOT / f"plug-ins/{args.maya}/Release/mmd_tools_cpp.mll"
    report = run_maya_e2e(
        project_root=ROOT, version=args.maya, out_dir=out, port=args.port, timeout=180,
        log_path=out / "probe.log", report_path=out / "report.json",
        command=("from tools.render_override.settings_e2e import run_probe\n"
                 f"run_probe({str(out)!r}, {str(plugin)!r})"),
        marker=MARKER, send_label="mmd-render-settings",
        stale_paths=(out / "probe.log", out / "report.json"),
        env_overrides={"MAYA_VP2_DEVICE_OVERRIDE": "VirtualDeviceDx11", "MMD_TOOLS_CPP_PLUGIN": str(plugin),
                       "PATH": str(plugin.parent) + os.pathsep + os.environ.get("PATH", "")},
    )
    print(json.dumps(report, indent=2))
    return 0 if report.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
