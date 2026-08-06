"""Verify the opt-in native VP2 ownership path in a live Maya GUI.

The native ``mmdFastLoad -vp2Ownership true`` path is deliberately separate
from the ordinary ``MFnMesh`` importer.  Maya standalone can verify node
creation, but only a GUI model panel drives ``MPxGeometryOverride`` render-item
preparation.  This runner launches an isolated Maya profile through
``commandPort``, imports the small alpha-overlap PMX, waits for the custom
override to prepare one item per transparent material, moves the camera,
updates one material alpha, and captures the resulting viewport images.

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


def _wait_for_witness(cmds: Any, shape_name: str, log: Any) -> str:
    """Refresh VP2 until the native render-item witness is ready."""
    witness = "pending"
    for attempt in range(20):
        cmds.refresh(force=True)
        time.sleep(0.25)
        witness = str(cmds.mmdRenderWitness(node=shape_name))
        log(f"witness attempt {attempt + 1}: {witness}")
        if witness.startswith("ready"):
            break
    return witness


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

        # Keep an ordinary Maya mesh in the same scene.  The opt-in native
        # shape must not replace or duplicate the regular scene path; this
        # control is also included in the viewport framing so disappearance
        # is visible in the captured witness images.
        control_result = cmds.polyCube(
            name="render_override_ordinary_control",
            constructionHistory=False,
            width=0.18,
            height=0.18,
            depth=0.18,
        )
        if not control_result:
            raise RuntimeError("could not create ordinary scene control cube")
        control_transform = str(control_result[0])
        cmds.xform(
            control_transform,
            worldSpace=True,
            translation=(1.0, 0.35, 0.0),
        )
        report["ordinaryControl"] = control_transform

        panels = [str(panel) for panel in (cmds.getPanel(type="modelPanel") or [])]
        if not panels:
            raise RuntimeError("Maya GUI has no modelPanel")
        panel = "modelPanel4" if "modelPanel4" in panels else panels[0]
        heads_up_display_before = {}
        for current in panels:
            try:
                heads_up_display_before[current] = bool(
                    cmds.modelEditor(current, query=True, headsUpDisplay=True)
                )
            except Exception as exc:
                log(f"HUD query warning for {current}: {exc}")
            cmds.modelEditor(
                current,
                edit=True,
                rendererName="vp2Renderer",
                displayAppearance="smoothShaded",
                displayTextures=False,
                wireframeOnShaded=False,
                grid=False,
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
        report["headsUpDisplay"] = {
            "before": heads_up_display_before,
            "afterSetup": {
                current: bool(
                    cmds.modelEditor(current, query=True, headsUpDisplay=True)
                )
                for current in panels
            },
        }
        report["renderer"] = {
            current: cmds.modelEditor(current, query=True, rendererName=True)
            for current in panels
        }

        try:
            # viewFit takes a camera/object target, not a modelPanel name.
            # Include only the custom shape and the ordinary control; fitting
            # all DAG nodes also includes Maya's default cameras/lights.
            cmds.select([shape_name, control_transform], replace=True)
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

        witness = _wait_for_witness(cmds, shape_name, log)
        report["witness"] = witness

        ordinary_meshes = [
            str(item) for item in (cmds.ls(type="mesh", long=True) or [])
        ]
        custom_meshes = [
            str(item)
            for item in (
                cmds.listRelatives(
                    root_name,
                    allDescendents=True,
                    type="mesh",
                    fullPath=True,
                )
                or []
            )
        ]
        report["sceneOwnership"] = {
            "ordinaryMeshCount": len(ordinary_meshes),
            "ordinaryMeshes": ordinary_meshes,
            "customShapeMeshDescendants": custom_meshes,
            "ordinaryControlExists": bool(cmds.objExists(control_transform)),
            "ordinaryControlVisible": bool(
                cmds.getAttr(f"{control_transform}.visibility")
            ),
        }

        # A custom shape must not consume or disable ordinary Maya selection.
        # Keep the probe intentionally small: selecting the ordinary control
        # proves the normal scene interaction path remains available while the
        # custom render items are owned by the VP2 override.
        cmds.select(control_transform, replace=True)
        selected_control = [str(item) for item in (cmds.ls(selection=True, long=True) or [])]
        cmds.select(clear=True)
        report["selection"] = {
            "selectedControl": selected_control,
            "controlSelectable": control_transform in selected_control
            or f"|{control_transform}" in selected_control,
        }

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

        # Camera motion must preserve the queue witness.  The slight move is
        # intentionally applied without touching the shape or its material
        # data, so a ready witness after the refresh demonstrates that the
        # queue survives a normal VP2 camera invalidation.
        initial_camera_translate = list(
            cmds.xform("persp", query=True, worldSpace=True, translation=True)
        )
        moved_camera_translate = [
            float(initial_camera_translate[0]) + 0.12,
            float(initial_camera_translate[1]) + 0.04,
            float(initial_camera_translate[2]),
        ]
        cmds.xform("persp", worldSpace=True, translation=moved_camera_translate)
        camera_motion_witness = _wait_for_witness(cmds, shape_name, log)
        camera_capture = _capture_view(
            cmds,
            output_dir / "native_vp2_ownership_camera_motion.png",
            panel,
            width,
            height,
        )
        report["captures"]["cameraMotion"] = str(camera_capture)

        # The alpha fixture has two transparent PMX materials.  Turning m0
        # opaque must rebuild the queue as Opaque[m0/s0],Transparent[m1/s1],
        # then restoring its authored alpha must return to the original order.
        queue_update_result = str(
            cmds.mmdRenderQueueUpdate(
                node=shape_name,
                materialIndex=0,
                alpha=1.0,
            )
        )
        queue_after_opaque = _wait_for_witness(cmds, shape_name, log)
        opaque_capture = _capture_view(
            cmds,
            output_dir / "native_vp2_ownership_queue_opaque.png",
            panel,
            width,
            height,
        )
        restore_result = str(
            cmds.mmdRenderQueueUpdate(
                node=shape_name,
                materialIndex=0,
                alpha=0.55,
            )
        )
        queue_after_restore = _wait_for_witness(cmds, shape_name, log)
        restored_capture = _capture_view(
            cmds,
            output_dir / "native_vp2_ownership_queue_restored.png",
            panel,
            width,
            height,
        )
        report["captures"]["queueOpaque"] = str(opaque_capture)
        report["captures"]["queueRestored"] = str(restored_capture)
        report["cameraMotion"] = {
            "translate": moved_camera_translate,
            "witness": camera_motion_witness,
        }
        report["queueUpdate"] = {
            "requestResult": queue_update_result,
            "afterOpaque": queue_after_opaque,
            "restoreResult": restore_result,
            "afterRestore": queue_after_restore,
        }
        expected_transparent_order = "Transparent[m0/s0],Transparent[m1/s1]"
        expected_opaque_order = "Opaque[m0/s0],Transparent[m1/s1]"
        report["checks"] = {
            "customShapeCreated": True,
            "transparentMaterialItemsPrepared": (
                witness.startswith("ready items=2")
                and expected_transparent_order in witness
            ),
            "materialIndexOrderPrepared": expected_transparent_order in witness,
            "drawPreparationReady": witness.startswith("ready"),
            "geometryBuffersPrepared": "geometry=vertices=" in witness
            and ",indices=" in witness,
            "captureCreated": capture.is_file() and capture.stat().st_size > 0,
            "cameraMotionPreserved": (
                camera_motion_witness.startswith("ready")
                and expected_transparent_order in camera_motion_witness
            ),
            "queueUpdateReordered": (
                queue_after_opaque.startswith("ready")
                and expected_opaque_order in queue_after_opaque
            ),
            "queueUpdateRestored": (
                queue_after_restore.startswith("ready")
                and expected_transparent_order in queue_after_restore
            ),
            "cameraCaptureCreated": camera_capture.is_file()
            and camera_capture.stat().st_size > 0,
            "queueOpaqueCaptureCreated": opaque_capture.is_file()
            and opaque_capture.stat().st_size > 0,
            "queueRestoredCaptureCreated": restored_capture.is_file()
            and restored_capture.stat().st_size > 0,
            "ordinarySceneControlVisible": report["sceneOwnership"][
                "ordinaryControlExists"
            ]
            and report["sceneOwnership"]["ordinaryControlVisible"],
            "noCustomMfnMeshDuplicate": not report["sceneOwnership"][
                "customShapeMeshDescendants"
            ],
            "selectionPreserved": report["selection"]["controlSelectable"],
            "hudPreserved": (
                report["headsUpDisplay"]["before"]
                == report["headsUpDisplay"]["afterSetup"]
            ),
        }
        if not report["checks"]["transparentMaterialItemsPrepared"]:
            raise RuntimeError(
                f"two transparent material items were not prepared: {witness}"
            )
        if not report["checks"]["materialIndexOrderPrepared"]:
            raise RuntimeError(f"material order was not prepared: {witness}")
        if not report["checks"]["drawPreparationReady"]:
            raise RuntimeError(f"VP2 render-item witness stayed pending: {witness}")
        if not report["checks"]["geometryBuffersPrepared"]:
            raise RuntimeError(f"VP2 geometry witness stayed pending: {witness}")
        if not report["checks"]["captureCreated"]:
            raise RuntimeError(f"VP2 capture was empty: {capture}")
        for check_name in (
            "cameraMotionPreserved",
            "queueUpdateReordered",
            "queueUpdateRestored",
            "cameraCaptureCreated",
            "queueOpaqueCaptureCreated",
            "queueRestoredCaptureCreated",
            "ordinarySceneControlVisible",
            "noCustomMfnMeshDuplicate",
            "selectionPreserved",
            "hudPreserved",
        ):
            if not report["checks"][check_name]:
                raise RuntimeError(
                    f"native VP2 ownership check failed: {check_name}"
                )
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
            out_dir / "native_vp2_ownership_camera_motion.png",
            out_dir / "native_vp2_ownership_camera_motion.0000.png",
            out_dir / "native_vp2_ownership_camera_motion.0001.png",
            out_dir / "native_vp2_ownership_queue_opaque.png",
            out_dir / "native_vp2_ownership_queue_opaque.0000.png",
            out_dir / "native_vp2_ownership_queue_opaque.0001.png",
            out_dir / "native_vp2_ownership_queue_restored.png",
            out_dir / "native_vp2_ownership_queue_restored.0000.png",
            out_dir / "native_vp2_ownership_queue_restored.0001.png",
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
