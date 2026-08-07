"""Exercise the opt-in native caster override in a live Maya viewport.

This probe intentionally uses the settings-backed UI import route, then toggles
``mmdNativeCaster`` through ``MRenderer``.  It records the native witness while
the override is active, captures the ordinary viewport before/after disabling
it, and verifies that disabling/unloading releases the private targets.
The R32F occupancy flag is evidence only; this is not a shadow-composition or
GoldenOracle parity claim.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import logging
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, Optional

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tests.viewport.maya_e2e_harness import run_maya_e2e  # noqa: E402
from tools.render_override_vp2_ownership_e2e import (  # noqa: E402
    _capture_view,
    _configure_camera,
    _configure_oracle_color_environment,
    _require_requested_plugin,
)


COMPLETION_MARKER = "//-- RENDER OVERRIDE NATIVE CASTER FINISHED --//"
DEFAULT_PORT = 7738
DEFAULT_TIMEOUT = 240.0
LOGGER = logging.getLogger(__name__)
DEFAULT_CAMERA: Dict[str, Any] = {
    "position": [0.04, 0.58, 3.4],
    "target": [0.04, 0.58, 0.0],
    "fov": 28.0,
    "near": 0.1,
    "far": 20.0,
}


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _query_witness(cmds: Any, log: Any) -> Dict[str, Any]:
    raw = str(cmds.mmdNativeCasterWitness())
    log(f"native caster witness: {raw}")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"native caster witness is not JSON: {raw!r}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"native caster witness is not an object: {value!r}")
    return value


def _wait_for_active_witness(cmds: Any, log: Any) -> Dict[str, Any]:
    witness: Dict[str, Any] = {}
    for attempt in range(30):
        cmds.refresh(force=True)
        time.sleep(0.3)
        witness = _query_witness(cmds, log)
        if witness.get("setup") or (
            witness.get("drawAttempted") and witness.get("frameComplete")
        ):
            return witness
        log(f"native caster setup pending (attempt {attempt + 1})")
    return witness


def _set_panel_override(mel: Any, panel: str, override_name: str) -> None:
    """Select a registered VP2 override for one model panel.

    ``MRenderer.setRenderOverrideName`` is batch-only in Maya 2024; viewport
    panels use the modelEditor ``-rom`` flag instead.
    """
    safe_panel = panel.replace('"', "")
    safe_name = override_name.replace('"', "")
    mel.eval(
        f'modelEditor -edit -rnm "vp2Renderer" -rom "{safe_name}" "{safe_panel}"'
    )


def _capture_active_viewport_buffer(
    cmds: Any, output_path: Path, panel: str
) -> tuple[Path, Dict[str, Any], bytes]:
    """Read the active VP2 color buffer while the caster override is selected."""
    import maya.api.OpenMaya as om
    import maya.api.OpenMayaUI as omui

    cmds.setFocus(panel)
    cmds.refresh(force=True)
    time.sleep(0.3)
    view = omui.M3dView.active3dView()
    image = om.MImage()
    view.readColorBuffer(image, True)
    width, height = [int(value) for value in image.getSize()]
    if width <= 0 or height <= 0:
        raise RuntimeError(f"active viewport readback has invalid size {width}x{height}")
    expected = width * height * 4
    pixels = image.pixels()
    if isinstance(pixels, int):
        if pixels <= 0:
            raise RuntimeError("active viewport readback returned a null pixel pointer")
        buffer = ctypes.string_at(pixels, expected)
    else:
        view = memoryview(pixels).cast("B")
        if view.nbytes < expected:
            raise RuntimeError(
                f"active viewport readback buffer too short: {view.nbytes} < {expected}"
            )
        buffer = view[:expected].tobytes()
    # Sample the RGBA8 readback to prove that the active present contains a
    # non-empty, non-uniform scene rather than a clear/private target.
    sample_step = max(1, (width * height) // 4096)
    non_black = 0
    distinct = set()
    samples = 0
    for pixel_index in range(0, width * height, sample_step):
        offset = pixel_index * 4
        red, green, blue, _ = buffer[offset : offset + 4]
        samples += 1
        if max(red, green, blue) > 8:
            non_black += 1
        if len(distinct) < 32:
            distinct.add((red, green, blue))
    if samples <= 0 or non_black == 0 or len(distinct) < 2:
        raise RuntimeError(
            "active viewport readback is empty or uniform "
            f"(samples={samples}, nonBlack={non_black}, distinct={len(distinct)})"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.writeToFile(str(output_path), "png")
    if not output_path.is_file() or output_path.stat().st_size <= 0:
        raise RuntimeError("active viewport readback did not create a PNG")
    stats = {
        "width": width,
        "height": height,
        "samples": samples,
        "nonBlackSamples": non_black,
        "distinctSamples": len(distinct),
        "pixelSha256": hashlib.sha256(buffer).hexdigest(),
        "scenePixelSha256": hashlib.sha256(
            b"".join(
                buffer[row * width * 4 : (row + 1) * width * 4]
                # MImage rows are bottom-origin while the written PNG is
                # top-origin; display-space y<90% therefore maps to raw rows
                # y>=10%.
                for row in range(max(1, int(height * 0.1)), height)
            )
        ).hexdigest(),
        "sceneRegionDisplayY": [0, int(height * 0.9)],
    }
    return output_path, stats, buffer


def run_probe(
    log_path: str,
    report_path: str,
    out_dir: str,
    model_path: str,
    plugin_path: str,
    width: int = 640,
    height: int = 480,
    camera_config: Optional[Dict[str, Any]] = None,
    frame: int = 0,
) -> None:
    """Run the Maya-side caster probe and always write a structured report."""
    import maya.cmds as cmds
    import maya.mel as mel
    import maya.api.OpenMayaRender as omr

    log_file = Path(log_path)
    report_file = Path(report_path)
    output_dir = Path(out_dir)
    report: dict[str, Any] = {
        "status": "fail",
        "claim": "native-caster-capability-witness-only",
        "model": str(model_path),
        "plugin": str(plugin_path),
        "captures": {},
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
        log("=== native caster probe begin ===")
        cmds.file(new=True, force=True)
        loaded_plugin = _require_requested_plugin(cmds, plugin_path, log)
        report["loadedPluginPath"] = str(loaded_plugin)
        report["vp2Device"] = str(cmds.ogs(deviceInformation=True))
        report["rendererOverrideCount"] = int(omr.MRenderer.renderOverrideCount())
        try:
            report["rendererFindOverride"] = bool(
                omr.MRenderer.findRenderOverride("mmdNativeCaster")
            )
        except Exception as exc:
            report["rendererFindOverride"] = f"ERR: {exc}"

        from mmd_tools.io.mmd_importer import import_mmd_file
        from mmd_tools.services.settings_service import SettingsService

        ui_options = SettingsService().build_pmx_import_options(
            custom_namespace="native_caster_e2e"
        )
        ui_options.update(
            import_physics=False,
            use_cpp_fast_load=True,
            use_cpp_vp2_ownership=True,
            cpp_fast_load_mesh_only=True,
        )
        root_result = import_mmd_file(str(Path(model_path).resolve()), options=ui_options)
        if not root_result:
            raise RuntimeError("UI import returned no root")
        root_name = str(root_result)
        shapes = [
            str(item)
            for item in (
                cmds.listRelatives(root_name, allDescendents=True, fullPath=True) or []
            )
            if cmds.nodeType(item) == "mmdRenderShape"
        ]
        if not shapes:
            raise RuntimeError(f"UI import did not create mmdRenderShape: {root_name}")
        report["root"] = root_name
        report["shapes"] = shapes
        log(f"UI import root={root_name} mmdRenderShape count={len(shapes)}")

        panels = [str(panel) for panel in (cmds.getPanel(type="modelPanel") or [])]
        if not panels:
            raise RuntimeError("Maya GUI has no modelPanel")
        camera = _configure_camera(cmds, panels, camera_config or DEFAULT_CAMERA)
        report["camera"] = camera
        parity_view = _configure_oracle_color_environment(cmds)
        report["parityView"] = parity_view
        if parity_view["errors"]:
            raise RuntimeError("color-management setup failed: " + "; ".join(parity_view["errors"]))
        for panel in panels:
            cmds.modelEditor(
                panel,
                edit=True,
                rendererName="vp2Renderer",
                displayAppearance="smoothShaded",
                displayTextures=True,
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
                useDefaultMaterial=False,
                selectionHiliteDisplay=False,
            )
        panel = "modelPanel4" if "modelPanel4" in panels else panels[0]
        cmds.setFocus(panel)
        report["panels"] = {
            current: {
                "rendererName": cmds.modelEditor(current, query=True, rendererName=True),
                "headsUpDisplay": bool(
                    cmds.modelEditor(current, query=True, headsUpDisplay=True)
                ),
            }
            for current in panels
        }
        available_overrides: Dict[str, Any] = {}
        for current in panels:
            try:
                available_overrides[current] = cmds.modelEditor(
                    current, query=True, rol=True
                )
            except Exception as exc:
                available_overrides[current] = f"ERR: {exc}"
        report["availableOverrides"] = available_overrides

        # Rebind the panel override after late plug-in registration.  Maya can
        # retain the modelEditor override token from panel creation even when
        # ``rol`` has been refreshed; an explicit clear/set pair forces the
        # live panel to instantiate the newly registered operation list.
        for current in panels:
            _set_panel_override(mel, current, "")
        cmds.refresh(force=True)
        for current in panels:
            _set_panel_override(mel, current, "mmdNativeCaster")
        report["overrideEnableReturned"] = True
        report["activeOverride"] = str(omr.MRenderer.activeRenderOverride())
        active_witness = _wait_for_active_witness(cmds, log)
        report["activeWitness"] = active_witness
        if not active_witness.get("setup") and not active_witness.get("frameComplete"):
            raise RuntimeError("native caster setup did not become active")
        if not active_witness.get("selectedCount"):
            raise RuntimeError("native caster selected no mmdRenderShape nodes")
        active_viewport_buffer: Optional[bytes] = None
        active_viewport_stats: Dict[str, Any] = {}
        try:
            report["captures"]["casterEnabled"] = str(
                _capture_view(
                    cmds,
                    output_dir / "native_caster_enabled.png",
                    panel,
                    width,
                    height,
                    frame,
                )
            )
        except Exception as exc:
            # A private R32F caster target is not a presentable viewport color
            # target on every Maya playblast path.  Keep the failure explicit;
            # target occupancy and the post-disable ordinary present are still
            # checked below.
            report["captures"]["casterEnabledError"] = str(exc)
        try:
            viewport_path, viewport_stats, active_viewport_buffer = (
                _capture_active_viewport_buffer(
                    cmds,
                    output_dir / "native_caster_enabled_viewport.png",
                    panel,
                )
            )
            active_viewport_stats = viewport_stats
            report["captures"]["casterEnabledViewport"] = str(viewport_path)
            report["captures"]["casterEnabledViewportStats"] = viewport_stats
        except Exception as exc:
            report["captures"]["casterEnabledViewportError"] = str(exc)
        report["activePanel"] = {
            "panel": panel,
            "rendererName": cmds.modelEditor(
                panel, query=True, rendererName=True
            ),
            "headsUpDisplay": bool(
                cmds.modelEditor(panel, query=True, headsUpDisplay=True)
            ),
        }

        for current in panels:
            _set_panel_override(mel, current, "")
        report["overrideDisableReturned"] = True
        report["activeOverrideAfterDisable"] = str(omr.MRenderer.activeRenderOverride())
        cmds.refresh(force=True)
        time.sleep(0.5)
        disabled_witness = _query_witness(cmds, log)
        report["disabledWitness"] = disabled_witness
        report["captures"]["casterDisabled"] = str(
            _capture_view(
                cmds,
                output_dir / "native_caster_disabled.png",
                panel,
                width,
                height,
                frame,
            )
        )
        try:
            disabled_viewport_path, disabled_viewport_stats, disabled_viewport_buffer = (
                _capture_active_viewport_buffer(
                    cmds, output_dir / "native_caster_disabled_viewport.png", panel
                )
            )
            report["captures"]["casterDisabledViewport"] = str(
                disabled_viewport_path
            )
            report["captures"]["casterDisabledViewportStats"] = (
                disabled_viewport_stats
            )
        except Exception as exc:
            report["captures"]["casterDisabledViewportError"] = str(exc)
            disabled_viewport_buffer = None
        if active_viewport_buffer is not None and disabled_viewport_buffer is not None:
            active_width = int(active_viewport_stats.get("width", 0))
            active_height = int(active_viewport_stats.get("height", 0))
            disabled_stats = report["captures"].get(
                "casterDisabledViewportStats", {}
            )
            disabled_width = int(disabled_stats.get("width", 0))
            disabled_height = int(disabled_stats.get("height", 0))
            if (active_width, active_height) == (disabled_width, disabled_height):
                differing_pixels = 0
                min_x, min_y = active_width, active_height
                max_x = max_y = -1
                for pixel_index in range(active_width * active_height):
                    offset = pixel_index * 4
                    if active_viewport_buffer[offset : offset + 4] == (
                        disabled_viewport_buffer[offset : offset + 4]
                    ):
                        continue
                    differing_pixels += 1
                    x = pixel_index % active_width
                    y = pixel_index // active_width
                    display_y = active_height - 1 - y
                    min_x = min(min_x, x)
                    min_y = min(min_y, display_y)
                    max_x = max(max_x, x)
                    max_y = max(max_y, display_y)
                report["captures"]["standardPresentDiff"] = {
                    "width": active_width,
                    "height": active_height,
                    "differingPixels": differing_pixels,
                    "ratio": differing_pixels / float(active_width * active_height),
                    "bbox": None
                    if max_x < 0
                    else [min_x, min_y, max_x, max_y],
                }
        if disabled_witness.get("setup"):
            raise RuntimeError("native caster remained setup after disable")
        if not disabled_witness.get("released"):
            raise RuntimeError("disabled caster witness did not report released targets")

        # Negative control: keep the override path enabled but hide every
        # caster shape.  The next setup resets per-frame occupancy, so a clear
        # target after this frame proves the sample did not come from stale
        # diagnostics or the standard scene.
        for shape in shapes:
            cmds.setAttr(f"{shape}.visibility", False)
        for current in panels:
            _set_panel_override(mel, current, "mmdNativeCaster")
        hidden_active_witness = _wait_for_active_witness(cmds, log)
        report["hiddenActiveWitness"] = hidden_active_witness
        for current in panels:
            _set_panel_override(mel, current, "")
        cmds.refresh(force=True)
        time.sleep(0.5)
        hidden_disabled_witness = _query_witness(cmds, log)
        report["hiddenDisabledWitness"] = hidden_disabled_witness
        if hidden_disabled_witness.get("occupied") or hidden_disabled_witness.get(
            "nonClearSamples"
        ):
            raise RuntimeError("hidden-shape caster witness reported non-clear occupancy")
        if not hidden_disabled_witness.get("released"):
            raise RuntimeError("hidden-shape caster witness did not report released targets")

        unload_error = None
        try:
            cmds.unloadPlugin("mmd_tools_cpp", force=True)
        except Exception as exc:  # Maya may keep a shape node alive until file close.
            unload_error = str(exc)
            cmds.file(new=True, force=True)
            try:
                cmds.unloadPlugin("mmd_tools_cpp", force=True)
            except Exception as retry_exc:
                unload_error = f"{unload_error}; retry: {retry_exc}"
        report["pluginUnloadError"] = unload_error
        report["pluginLoadedAfterUnload"] = bool(
            cmds.pluginInfo("mmd_tools_cpp", query=True, loaded=True)
        )
        checks = {
            "overrideRegistered": bool(active_witness.get("registered")),
            "casterSelectedMmdRenderShape": int(active_witness.get("selectedCount", 0)) > 0,
            "r32fTargetAcquired": bool(active_witness.get("colorTargetAcquired"))
            and int(active_witness.get("colorWidth", 0)) == 2048
            and int(active_witness.get("colorHeight", 0)) == 2048,
            "d32TargetAcquired": bool(active_witness.get("depthTargetAcquired"))
            and int(active_witness.get("depthWidth", 0)) == 2048
            and int(active_witness.get("depthHeight", 0)) == 2048,
            "shaderBound": bool(active_witness.get("shaderAvailable")),
            "matrixBound": bool(active_witness.get("matrixBound")),
            "operationInsertedBeforeScene": bool(
                active_witness.get("operationInsertedBeforeScene")
            ),
            "occupancySupported": bool(active_witness.get("occupancySupported")),
            "occupied": bool(active_witness.get("occupied"))
            and int(active_witness.get("nonClearSamples", 0)) > 0,
            "ordinarySceneAfterDisable": Path(report["captures"]["casterDisabled"]).is_file(),
            "activeViewportReadback": Path(
                report["captures"].get("casterEnabledViewport", "")
            ).is_file()
            and not bool(report["captures"].get("casterEnabledViewportError"))
            and bool(report["captures"].get("casterEnabledViewportStats"))
            and int(
                report["captures"].get("casterEnabledViewportStats", {}).get(
                    "distinctSamples", 0
                )
            )
            >= 2,
            "disabledViewportReadback": Path(
                report["captures"].get("casterDisabledViewport", "")
            ).is_file()
            and not bool(report["captures"].get("casterDisabledViewportError"))
            and bool(report["captures"].get("casterDisabledViewportStats"))
            and int(
                report["captures"].get("casterDisabledViewportStats", {}).get(
                    "distinctSamples", 0
                )
            )
            >= 2,
            "standardPresentPreserved": (
                bool(report["captures"].get("standardPresentDiff"))
                and float(
                    report["captures"]["standardPresentDiff"].get("ratio", 1.0)
                )
                <= 0.001
                and report["captures"].get("casterEnabledViewportStats", {}).get(
                    "scenePixelSha256"
                )
                == report["captures"].get("casterDisabledViewportStats", {}).get(
                    "scenePixelSha256"
                )
            ),
            "disableReleased": bool(hidden_disabled_witness.get("released")),
            "hiddenNegativeClear": not bool(hidden_disabled_witness.get("occupied"))
            and int(hidden_disabled_witness.get("nonClearSamples", 0)) == 0,
            "unloaded": not report["pluginLoadedAfterUnload"],
        }
        report["checks"] = checks
        failed = [name for name, passed in checks.items() if not passed]
        if failed:
            raise RuntimeError("native caster checks failed: " + ", ".join(failed))
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
    """Launch Maya, run the native caster probe, and return its status."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maya", default="2024", help="Maya major version.")
    parser.add_argument("--model", type=Path, required=True, help="UI-import PMX fixture.")
    parser.add_argument("--plugin", type=Path, default=None)
    parser.add_argument(
        "--out-dir", type=Path, default=_ROOT / "build" / "render-override-native-caster"
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--frame", type=int, default=0)
    parser.add_argument("--camera-json", type=Path, default=None)
    args = parser.parse_args()

    if not args.model.is_file():
        parser.error(f"model does not exist: {args.model}")
    plugin = args.plugin or (
        _ROOT / "plug-ins" / str(args.maya) / "Debug" / "mmd_tools_cpp.mll"
    )
    if not plugin.is_file():
        parser.error(f"native plug-in does not exist: {plugin}")
    camera_config = DEFAULT_CAMERA
    if args.camera_json is not None:
        try:
            camera_config = json.loads(args.camera_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            parser.error(f"could not read --camera-json: {exc}")
        if not isinstance(camera_config, dict):
            parser.error("--camera-json must contain a JSON object")

    out_dir = args.out_dir.resolve()
    log_path = out_dir / f"native_caster_maya{args.maya}.log"
    report_path = out_dir / f"native_caster_maya{args.maya}.json"
    command = (
        "from tools.render_override_native_caster_e2e import run_probe\n"
        f"run_probe({str(log_path)!r}, {str(report_path)!r}, {str(out_dir)!r}, "
        f"{str(args.model.resolve())!r}, {str(plugin.resolve())!r}, "
        f"width={args.width}, height={args.height}, camera_config={camera_config!r}, "
        f"frame={args.frame})\n"
    )
    env_overrides = {
        "MAYA_VP2_DEVICE_OVERRIDE": "VirtualDeviceDx11",
        "MMD_TOOLS_CPP_PLUGIN": str(plugin.resolve()),
        "PATH": os.pathsep.join((str(plugin.parent), os.environ.get("PATH", ""))),
    }
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
        send_label="<render-override-native-caster-command>",
        stale_paths=(
            log_path,
            report_path,
            out_dir / "native_caster_enabled.png",
            out_dir / "native_caster_enabled_viewport.png",
            out_dir / "native_caster_disabled.png",
        ),
        port_error=f"commandPort :{args.port} is already open; choose another --port",
        report_error=f"native caster report missing: {report_path}",
        log_ready=LOGGER,
        warn_detached=True,
        env_overrides=env_overrides,
    )
    LOGGER.info("Native caster E2E status: %s", report.get("status"))
    return 0 if report.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
