"""Verify the opt-in native VP2 ownership path in a live Maya GUI.

The native ``mmdFastLoad -vp2Ownership true`` path is deliberately separate
from the ordinary ``MFnMesh`` importer.  Maya standalone can verify node
creation, but only a GUI model panel drives ``MPxGeometryOverride`` render-item
preparation.  This runner launches an isolated Maya profile through
``commandPort``, imports the small alpha-overlap PMX, waits for the custom
override to prepare its pass items, and captures one viewport image.

The resulting ``witness`` is draw-preparation evidence.  It does not claim
alpha-blend visual parity, GoldenOracle parity, or self-shadow composition.

Example::

    mayapy tools/render_override_vp2_ownership_e2e.py --maya 2024 \
        --model "F:/path/to/mmd-alpha-blend-overlap.pmx"
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


_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tests.viewport.maya_e2e_harness import run_maya_e2e  # noqa: E402


COMPLETION_MARKER = "//-- RENDER OVERRIDE VP2 OWNERSHIP FINISHED --//"
DEFAULT_PORT = 7734
DEFAULT_TIMEOUT = 180.0
LOGGER = logging.getLogger(__name__)


def _write_report(path: Path, report: dict[str, Any]) -> None:
    """Persist a UTF-8 report for the host-side commandPort harness."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _capture_view(
    cmds: Any, destination: Path, panel: str, width: int, height: int
) -> Path:
    """Capture the active GUI viewport and return the generated PNG path."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = cmds.playblast(
        filename=str(destination.with_suffix("")),
        frame=1,
        format="image",
        compression="png",
        viewer=False,
        showOrnaments=False,
        forceOverwrite=True,
        offScreen=False,
        percent=100,
        width=width,
        height=height,
        editorPanelName=panel,
    )
    candidates = (
        destination,
        destination.with_suffix(".png"),
        destination.parent / f"{destination.stem}.0000.png",
        destination.parent / f"{destination.stem}.0001.png",
    )
    for candidate in candidates:
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    generated = sorted(
        destination.parent.glob(f"{destination.stem}*.png"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if generated:
        return generated[0]
    raise RuntimeError(f"playblast did not create a PNG: {result!r}")


def run_probe(
    log_path: str,
    report_path: str,
    out_dir: str,
    model_path: str,
    plugin_path: str,
    width: int = 640,
    height: int = 480,
) -> None:
    """Run the Maya-side native ownership probe and always write its report."""
    import maya.cmds as cmds

    log_file = Path(log_path)
    report_file = Path(report_path)
    output_dir = Path(out_dir)
    report: dict[str, Any] = {
        "status": "fail",
        "model": str(model_path),
        "plugin": str(plugin_path),
        "witness": "not-run",
        "captures": {},
        "renderer": {},
        "claim": "vp2-draw-preparation-only",
        "visualParity": "not-run",
        "errors": [],
    }

    def log(message: object) -> None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a", encoding="utf-8") as stream:
            stream.write(f"{message}\n")
        try:
            print(message)
        except Exception:
            pass

    try:
        log("=== native VP2 ownership probe begin ===")
        cmds.file(new=True, force=True)
        plugin = str(Path(plugin_path).resolve())
        if not cmds.pluginInfo(plugin, query=True, loaded=True):
            cmds.loadPlugin(plugin, quiet=False)
        log(f"plugin loaded: {cmds.pluginInfo(plugin, query=True, loaded=True)}")
        log(f"vp2 device: {cmds.ogs(deviceInformation=True)}")

        result = cmds.mmdFastLoad(
            file=str(Path(model_path).resolve()),
            name="render_override_vp2_ownership",
            vp2Ownership=True,
        )
        if not result or len(result) < 2:
            raise RuntimeError(f"mmdFastLoad returned no shape: {result!r}")
        root_name, shape_name = str(result[0]), str(result[-1])
        report["root"] = root_name
        report["shape"] = shape_name
        log(f"created root={root_name} shape={shape_name}")

        panels = [str(panel) for panel in (cmds.getPanel(type="modelPanel") or [])]
        if not panels:
            raise RuntimeError("Maya GUI has no modelPanel")
        panel = "modelPanel4" if "modelPanel4" in panels else panels[0]
        for current in panels:
            cmds.modelEditor(
                current,
                edit=True,
                rendererName="vp2Renderer",
                displayAppearance="smoothShaded",
                displayTextures=False,
                wireframeOnShaded=False,
                grid=False,
                headsUpDisplay=False,
                cameras=False,
                lights=False,
                locators=False,
                joints=False,
                ikHandles=False,
                deformers=False,
                dynamics=False,
                nurbsCurves=False,
            )
            cmds.lookThru(current, "persp")
        report["panel"] = panel
        report["renderer"] = {
            current: cmds.modelEditor(current, query=True, rendererName=True)
            for current in panels
        }

        try:
            # viewFit takes a camera/object target, not a modelPanel name.
            # The panel already looks through persp above.  Select only the
            # custom shape for the fit; fitting all DAG nodes also includes
            # Maya's default cameras/lights and can produce a blank-looking
            # capture even when render-item preparation is ready.
            cmds.select(shape_name, replace=True)
            cmds.viewFit("persp", all=False, animate=False, fitFactor=0.8)
        except Exception as exc:
            log(f"viewFit warning: {exc}")
        finally:
            cmds.select(clear=True)
        try:
            cmds.setFocus(panel)
        except Exception:
            pass

        try:
            report["worldBounds"] = list(cmds.exactWorldBoundingBox(shape_name))
            report["camera"] = {
                "translate": list(
                    cmds.xform("persp", query=True, worldSpace=True, translation=True)
                ),
                "rotate": list(
                    cmds.xform("persp", query=True, worldSpace=True, rotation=True)
                ),
            }
            log(f"world bounds: {report['worldBounds']} camera: {report['camera']}")
        except Exception as exc:
            log(f"camera/bounds query warning: {exc}")

        witness = "pending"
        for attempt in range(20):
            cmds.refresh(force=True)
            time.sleep(0.25)
            witness = str(cmds.mmdRenderWitness(node=shape_name))
            log(f"witness attempt {attempt + 1}: {witness}")
            if witness.startswith("ready"):
                break
        report["witness"] = witness

        cmds.refresh(force=True)
        time.sleep(0.5)

        capture = _capture_view(
            cmds,
            output_dir / "native_vp2_ownership.png",
            panel,
            width,
            height,
        )
        report["captures"]["ownership"] = str(capture)
        report["checks"] = {
            "customShapeCreated": True,
            "transparentPassPrepared": "Transparent" in witness,
            "drawPreparationReady": witness.startswith("ready"),
            "geometryBuffersPrepared": "geometry=vertices=" in witness
            and ",indices=" in witness,
            "captureCreated": capture.is_file() and capture.stat().st_size > 0,
        }
        if not report["checks"]["transparentPassPrepared"]:
            raise RuntimeError(f"transparent pass was not prepared: {witness}")
        if not report["checks"]["drawPreparationReady"]:
            raise RuntimeError(f"VP2 render-item witness stayed pending: {witness}")
        if not report["checks"]["geometryBuffersPrepared"]:
            raise RuntimeError(f"VP2 geometry witness stayed pending: {witness}")
        if not report["checks"]["captureCreated"]:
            raise RuntimeError(f"VP2 capture was empty: {capture}")
        report["status"] = "pass"
    except Exception as exc:
        report["errors"].append(str(exc))
        log("probe failed:")
        log(traceback.format_exc())
    finally:
        _write_report(report_file, report)
        log(f"RESULT_JSON: {json.dumps(report, ensure_ascii=False)}")
        log(COMPLETION_MARKER)


def main() -> int:
    """Launch Maya, run the native VP2 probe, and return its status."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maya", default="2024", help="Maya major version.")
    parser.add_argument("--model", type=Path, required=True, help="Alpha-overlap PMX fixture.")
    parser.add_argument(
        "--plugin",
        type=Path,
        default=None,
        help="Native plug-in path (defaults to plug-ins/<maya>/Debug).",
    )
    parser.add_argument("--out-dir", type=Path, default=_ROOT / "build" / "render-override-vp2")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    args = parser.parse_args()

    if not args.model.is_file():
        parser.error(f"model does not exist: {args.model}")
    if args.width <= 0 or args.height <= 0:
        parser.error("--width and --height must be positive")

    plugin = args.plugin or (
        _ROOT / "plug-ins" / str(args.maya) / "Debug" / "mmd_tools_cpp.mll"
    )
    if not plugin.is_file():
        parser.error(f"native plug-in does not exist: {plugin}")

    out_dir = args.out_dir.resolve()
    log_path = out_dir / f"render_override_vp2_maya{args.maya}.log"
    report_path = out_dir / f"render_override_vp2_maya{args.maya}.json"
    command = (
        "from tools.render_override_vp2_ownership_e2e import run_probe\n"
        f"run_probe({str(log_path)!r}, {str(report_path)!r}, {str(out_dir)!r}, "
        f"{str(args.model.resolve())!r}, {str(plugin.resolve())!r}, "
        f"width={args.width}, height={args.height})\n"
    )
    report = run_maya_e2e(
        project_root=_ROOT,
        version=str(args.maya),
        out_dir=out_dir,
        port=args.port,
        timeout=args.timeout,
        log_path=log_path,
        report_path=report_path,
        command=command,
        marker=COMPLETION_MARKER,
        send_label="<render-override-vp2-ownership-command>",
        stale_paths=(
            log_path,
            report_path,
            out_dir / "native_vp2_ownership.png",
            out_dir / "native_vp2_ownership.0000.png",
            out_dir / "native_vp2_ownership.0001.png",
        ),
        port_error=f"commandPort :{args.port} is already open; choose another --port",
        report_error=f"VP2 ownership report missing: {report_path}",
        log_ready=LOGGER,
        warn_detached=True,
        env_overrides={
            "MAYA_VP2_DEVICE_OVERRIDE": "VirtualDeviceDx11",
            # Maya's GUI loader does not inherit the mayapy-side PATH used by
            # the standalone smoke.  Keep the native plug-in and mmd-anim DLL
            # directory ahead of the inherited search path.
            "PATH": os.pathsep.join((str(plugin.parent), os.environ.get("PATH", ""))),
        },
    )
    LOGGER.info("Native VP2 ownership E2E status: %s", report.get("status"))
    return 0 if report.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
