"""Real Maya GUI smoke for native DX11 drawing and OpenGL fallback.

This is intentionally an acceptance smoke, not a visual-parity gate.  It
proves only the ready/source-visibility contract and captures shaded and wire
viewport evidence.  Component picking is deliberately outside its scope.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.viewport.maya_e2e_harness import run_maya_e2e  # noqa: E402
from tools.render_override.common import (  # noqa: E402
    capture_view,
    require_requested_plugin,
    write_report,
)


MARKER = "//-- MAYA RENDER OVERRIDE GUI SMOKE FINISHED --//"
DEFAULT_PORT = 7738
LOGGER = logging.getLogger(__name__)


def _wait_ready(cmds: Any, shape: str, log: Any) -> str:
    witness = "pending"
    for attempt in range(24):
        cmds.refresh(force=True)
        time.sleep(0.25)
        witness = str(cmds.mmdRenderWitness(node=shape))
        log(f"witness {attempt + 1}: {witness}")
        if witness.startswith("ready"):
            return witness
    return witness


def _surface_hit(panel: str) -> list[str]:
    """Return DAG paths selected by a real center-of-viewport surface hit."""
    import maya.OpenMaya as om
    import maya.OpenMayaUI as omui
    import maya.cmds as cmds

    cmds.setFocus(panel)
    view = omui.M3dView.active3dView()
    x, y = int(view.portWidth()) // 2, int(view.portHeight()) // 2
    om.MGlobal.clearSelectionList()
    om.MGlobal.selectFromScreen(
        x, y, x, y, om.MGlobal.kReplaceList, om.MGlobal.kSurfaceSelectMethod
    )
    selection = om.MSelectionList()
    om.MGlobal.getActiveSelectionList(selection)
    names: list[str] = []
    for index in range(selection.length()):
        path = om.MDagPath()
        try:
            selection.getDagPath(index, path)
        except RuntimeError:
            continue
        names.append(path.fullPathName())
    return names


def run_probe(log_path: str, report_path: str, out_dir: str, model: str,
              plugin: str, width: int = 640, height: int = 480,
              vp2_backend: str = "dx11") -> None:
    """Execute the GUI-only side through commandPort and always emit JSON."""
    import maya.cmds as cmds
    from mmd_tools.io.cpp_fast_importer import _require_dx11_for_vp2_ownership

    log_file, report_file, output_dir = Path(log_path), Path(report_path), Path(out_dir)
    report: dict[str, Any] = {
        "status": "fail", "model": model, "plugin": plugin, "checks": {},
        "captures": {}, "selection": {"componentSelection": "not-run"},
        "vp2Backend": vp2_backend, "errors": [],
    }

    def log(value: object) -> None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write(f"{value}\n")

    try:
        cmds.file(new=True, force=True)
        report["loadedPluginPath"] = str(require_requested_plugin(cmds, plugin, log))
        device = str(cmds.ogs(deviceInformation=True))
        report["vp2Device"] = device
        if vp2_backend == "dx11":
            if "DirectX V.11" not in device and "DirectX11" not in device:
                raise RuntimeError(f"DX11 VP2 device required, got: {device}")
            _require_dx11_for_vp2_ownership(cmds)
            report["vp2Preflight"] = "accepted"
        elif "OpenGL" not in device:
            raise RuntimeError(f"OpenGL VP2 device required, got: {device}")
        else:
            try:
                _require_dx11_for_vp2_ownership(cmds)
            except RuntimeError as exc:
                report["vp2Preflight"] = str(exc)
            else:
                raise RuntimeError("OpenGL VP2 preflight did not reject the device")

        result = cmds.mmdFastLoad(
            file=str(Path(model).resolve()), name="render_override_gui_smoke",
            vp2Ownership=True,
        )
        if not result or len(result) != 3:
            raise RuntimeError(f"mmdFastLoad(vp2Ownership=True) failed: {result!r}")
        root, source, shape = map(str, result)
        report.update(root=root, sourceMesh=source, renderShape=shape)
        if cmds.nodeType(shape) != "mmdRenderShape":
            raise RuntimeError(f"unexpected proxy node type: {cmds.nodeType(shape)!r}")
        if not cmds.isConnected(f"{source}.outMesh", f"{shape}.inputMesh"):
            raise RuntimeError("proxy inputMesh is not connected to source outMesh")

        panels = cmds.getPanel(type="modelPanel") or []
        if not panels:
            raise RuntimeError("Maya GUI has no modelPanel")
        panel = "modelPanel4" if "modelPanel4" in panels else str(panels[0])
        cmds.modelEditor(panel, edit=True, rendererName="vp2Renderer",
                         displayAppearance="smoothShaded", displayTextures=True,
                         wireframeOnShaded=False, grid=False)
        cmds.lookThru(panel, "persp")
        cmds.select(shape, replace=True)
        cmds.viewFit("persp", all=False, animate=False, fitFactor=0.8)
        cmds.select(clear=True)
        if vp2_backend == "dx11":
            witness = _wait_ready(cmds, shape, log)
        else:
            # The bundled native effect is HLSL-only.  On OpenGL the geometry
            # override must not initialize, leaving the connected source mesh
            # as the visible compatibility path.
            cmds.refresh(force=True)
            time.sleep(0.5)
            witness = str(cmds.mmdRenderWitness(node=shape))
        source_hidden = not bool(cmds.getAttr(f"{source}.visibility"))
        shaded = capture_view(cmds, output_dir / "render_override_gui_shaded.png",
                              panel, width, height)

        cmds.modelEditor(panel, edit=True, displayAppearance="wireframe")
        cmds.refresh(force=True)
        wire = capture_view(cmds, output_dir / "render_override_gui_wire.png",
                            panel, width, height)
        picked = _surface_hit(panel)
        object_hit = any(
            name == root or name == shape or name.startswith(root + "|")
            for name in picked
        )
        report.update(panel=panel, witness=witness, sourceVisible=not source_hidden)
        report["captures"] = {"smoothShaded": str(shaded), "wireframe": str(wire)}
        report["selection"].update({
            "shapeMask": "mesh-object", "renderItemMask": "mesh-object",
            "objectHitTest": "pass" if object_hit else "fail",
            "surfaceHitSelection": picked,
            "componentSelection": "not-supported",
        })
        report["checks"] = {
            "requestedVp2Device": True,
            "proxyInputConnected": True,
            "smoothCapture": shaded.is_file() and shaded.stat().st_size > 0,
            "wireCapture": wire.is_file() and wire.stat().st_size > 0,
            "objectHitTest": object_hit,
        }
        if vp2_backend == "dx11":
            report["checks"].update({
                "dx11": True,
                "nativeDrawPreparationReady": witness.startswith("ready"),
                "sourceHiddenAfterReady": source_hidden,
            })
        else:
            report["checks"].update({
                "openGL": True,
                "pythonVp2PreflightRejected": (
                    "requires DirectX 11" in report["vp2Preflight"]
                    and "restart Maya" in report["vp2Preflight"]
                ),
                "nativeDrawSkippedOnUnsupportedApi": not witness.startswith("ready"),
                "sourceVisibleOnUnsupportedApi": not source_hidden,
            })
        failed = [name for name, ok in report["checks"].items() if not ok]
        if failed:
            raise RuntimeError("GUI smoke checks failed: " + ", ".join(failed))
        report["status"] = "pass"
    except Exception as exc:
        report["errors"].append(str(exc))
        log(traceback.format_exc())
    finally:
        write_report(report_file, report)
        log("RESULT_JSON: " + json.dumps(report, ensure_ascii=False))
        log(MARKER)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maya", default="2024")
    parser.add_argument("--model", type=Path, default=ROOT / "tests" / "data" / "mmt_test_model.pmx")
    parser.add_argument("--plugin", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "build" / "reports" / "render-override-gui-smoke")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--vp2-device", choices=("dx11", "glcore"), default="dx11")
    args = parser.parse_args()
    if not args.model.is_file():
        parser.error(f"model does not exist: {args.model}")
    plugin = args.plugin or ROOT / "plug-ins" / str(args.maya) / "Debug" / "mmd_tools_cpp.mll"
    if not plugin.is_file():
        parser.error(f"plugin does not exist: {plugin}")
    out = args.out_dir.resolve()
    log_path, report_path = out / f"maya{args.maya}.log", out / f"maya{args.maya}.json"
    command = (
        "from tools.smoke.maya_render_override_gui_smoke import run_probe\n"
        f"run_probe({str(log_path)!r}, {str(report_path)!r}, {str(out)!r}, "
        f"{str(args.model.resolve())!r}, {str(plugin.resolve())!r}, {args.width}, {args.height}, "
        f"{args.vp2_device!r})\n"
    )
    report = run_maya_e2e(
        project_root=ROOT, version=str(args.maya), out_dir=out, port=args.port,
        timeout=args.timeout, log_path=log_path, report_path=report_path,
        command=command, marker=MARKER, send_label="<render-override-gui-smoke>",
        stale_paths=(log_path, report_path, out / "render_override_gui_shaded.png",
                     out / "render_override_gui_shaded.0000.png", out / "render_override_gui_wire.png",
                     out / "render_override_gui_wire.0000.png"),
        port_error=f"commandPort :{args.port} is already open; choose another --port",
        report_error=f"GUI smoke report missing: {report_path}", log_ready=LOGGER,
        warn_detached=True,
        env_overrides={"MAYA_VP2_DEVICE_OVERRIDE": (
                           "VirtualDeviceDx11"
                           if args.vp2_device == "dx11"
                           else "VirtualDeviceGLCore"
                       ),
                       "MMD_TOOLS_CPP_PLUGIN": str(plugin.resolve()),
                       "PATH": os.pathsep.join((str(plugin.parent), os.environ.get("PATH", "")))},
    )
    return 0 if report.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
