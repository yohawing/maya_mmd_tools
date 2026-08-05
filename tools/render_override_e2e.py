"""Run the R1 passthrough RenderOverride gate in a real Maya GUI session.

The runner launches an isolated Maya profile through commandPort, captures the
same scene before and while the opt-in override is active, and compares the
two PNGs on the host.  The optional target probe adds conservative caster
draw/readback evidence; it does not claim material or self-shadow parity.
"""

from __future__ import annotations

import argparse
import json
import logging
import struct
import sys
import traceback
import zlib
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tests.viewport.maya_e2e_harness import run_maya_e2e  # noqa: E402


COMPLETION_MARKER = "//-- RENDER OVERRIDE E2E FINISHED --//"
DEFAULT_PORT = 7731
DEFAULT_TIMEOUT = 240.0
LOGGER = logging.getLogger(__name__)


def _write_report(path: Path, report: dict) -> None:
    """Persist a UTF-8 report for the host-side commandPort harness."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _capture_current_view(cmds, destination: Path) -> Path:
    """Capture the active GUI viewport and return Maya's actual PNG path."""
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
        width=640,
        height=480,
    )
    candidates = [
        destination,
        destination.with_suffix(".png"),
        destination.parent / f"{destination.stem}.0000.png",
        destination.parent / f"{destination.stem}.0001.png",
        destination.parent / f"{destination.stem}.1.png",
    ]
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
    raise RuntimeError(f"playblast did not create a PNG for {destination}: {result!r}")


def _renderer_by_panel(cmds) -> dict[str, str]:
    """Return the renderer selection for every currently available model panel."""
    panels = cmds.getPanel(type="modelPanel") or []
    if not panels:
        raise RuntimeError("no modelPanel is available for the R1 VP2 gate")
    return {panel: cmds.modelEditor(panel, query=True, rendererName=True) for panel in panels}


def _set_renderer_by_panel(cmds, renderer_name: str) -> dict[str, str]:
    """Select one renderer for every model panel and verify the request stuck."""
    for panel in _renderer_by_panel(cmds):
        cmds.modelEditor(panel, edit=True, rendererName=renderer_name)
    selected = _renderer_by_panel(cmds)
    if any(renderer != renderer_name for renderer in selected.values()):
        raise RuntimeError(f"renderer selection failed for {renderer_name}: {selected}")
    return selected


def _restore_renderer_by_panel(cmds, renderer_by_panel: dict[str, str]) -> dict[str, str]:
    """Restore each model panel's pre-gate renderer selection."""
    existing_panels = set(cmds.getPanel(type="modelPanel") or [])
    for panel, renderer_name in renderer_by_panel.items():
        if panel in existing_panels:
            cmds.modelEditor(panel, edit=True, rendererName=renderer_name)
    return _renderer_by_panel(cmds)


def _override_by_panel(omui, panels: list[str]) -> dict[str, str | None]:
    """Return each VP2 model panel's active render override name."""
    return {
        panel: (omui.M3dView.getM3dViewFromModelPanel(panel).renderOverrideName() or None)
        for panel in panels
    }


def _set_override_by_panel(omui, override_name: str, panels: list[str]) -> dict[str, str | None]:
    """Set a registered override through M3dView, the interactive API."""
    for panel in panels:
        omui.M3dView.getM3dViewFromModelPanel(panel).setRenderOverrideName(override_name)
    selected = _override_by_panel(omui, panels)
    expected = override_name or None
    if any(name != expected for name in selected.values()):
        raise RuntimeError(f"render override selection failed for {override_name!r}: {selected}")
    return selected


def _validate_target_probe_caster_selection(
    caster_selection: object, *, require_components: bool = False
) -> None:
    """Validate the routing-only caster-selection diagnostic shape.

    ``require_components`` is enabled only for the explicit real-PMX path.
    The original empty-scene target probe intentionally remains green with a
    zero-component ``empty`` result.
    """
    if not isinstance(caster_selection, dict) or any(
        key not in caster_selection
        for key in ("status", "reason", "components", "count")
    ) or not isinstance(caster_selection.get("components"), list) or (
        caster_selection.get("count")
        != len(caster_selection.get("components", ()))
    ):
        raise RuntimeError(
            "target probe caster-selection diagnostic is missing: "
            f"{caster_selection!r}"
        )
    if require_components and (
        caster_selection.get("status") != "ok"
        or caster_selection.get("count", 0) < 1
    ):
        raise RuntimeError(
            "real PMX target probe did not select caster components: "
            f"{caster_selection!r}"
        )


def _validate_target_probe_occupancy(
    target_report: object, *, require_components: bool = False
) -> None:
    """Validate occupancy evidence without upgrading unsupported to green.

    The D32 result is a draw witness for the target pair; the nested R32F
    result is kept explicit because an all-clear color attachment cannot prove
    that a regular material wrote a useful shadow value.
    """
    if not isinstance(target_report, dict):
        raise RuntimeError(f"target probe occupancy diagnostic is missing: {target_report!r}")
    occupancy = target_report.get("occupancy")
    color_occupancy = target_report.get("colorOccupancy")
    depth_occupancy = target_report.get("depthOccupancy")
    for label, value in (
        ("occupancy", occupancy),
        ("colorOccupancy", color_occupancy),
        ("depthOccupancy", depth_occupancy),
    ):
        if not isinstance(value, dict) or not isinstance(value.get("status"), str):
            raise RuntimeError(f"target probe {label} diagnostic is missing: {value!r}")
    valid_statuses = {"not-run", "unsupported", "empty", "occupied"}
    if any(value["status"] not in valid_statuses for value in (occupancy, color_occupancy, depth_occupancy)):
        raise RuntimeError(f"target probe occupancy has an invalid state: {target_report!r}")
    if require_components and occupancy["status"] == "not-run":
        raise RuntimeError(
            "real PMX target probe did not produce occupancy evidence: "
            f"{target_report!r}"
        )


def _validate_r32f_binding_probe(binding_probe: object) -> None:
    """Require successful binding lifecycle without claiming receiver rendering."""
    required = {
        "enabled",
        "status",
        "reason",
        "targetName",
        "parameter",
        "bindingAttemptCount",
        "bindingSucceeded",
        "releaseAttemptCount",
        "releaseSucceeded",
        "drawsReceiver",
    }
    if not isinstance(binding_probe, dict) or not required.issubset(binding_probe):
        raise RuntimeError(f"R32F binding-probe diagnostic is missing: {binding_probe!r}")
    if binding_probe["status"] not in {"not-run", "bound", "released", "unsupported"}:
        raise RuntimeError(f"R32F binding-probe has an invalid state: {binding_probe!r}")
    if binding_probe["drawsReceiver"] is not False:
        raise RuntimeError(
            "R32F binding probe unexpectedly claimed receiver drawing: "
            f"{binding_probe!r}"
        )
    if (
        binding_probe["bindingAttemptCount"] < 1
        or binding_probe["bindingSucceeded"] is not True
        or binding_probe["releaseAttemptCount"] < 1
        or binding_probe["releaseSucceeded"] is not True
        or binding_probe["status"] != "released"
    ):
        raise RuntimeError(
            "R32F binding probe did not complete a successful bind/release lifecycle: "
            f"{binding_probe!r}"
        )


def _validate_r32f_caster_pass(caster_pass: object) -> None:
    """Require a successful caster shader lifecycle without receiver claims."""
    required = {
        "enabled",
        "status",
        "reason",
        "targetNames",
        "shaderPath",
        "technique",
        "requiredParameter",
        "createAttemptCount",
        "createSucceeded",
        "releaseAttemptCount",
        "releaseSucceeded",
        "releaseBeforeTarget",
        "drawsReceiver",
        "claimsSelfShadow",
        "receiverComposition",
    }
    if not isinstance(caster_pass, dict) or not required.issubset(caster_pass):
        raise RuntimeError(f"R32F caster-pass diagnostic is missing: {caster_pass!r}")
    if caster_pass["status"] not in {"created", "released", "unsupported", "not-run"}:
        raise RuntimeError(f"R32F caster-pass has an invalid state: {caster_pass!r}")
    if any(caster_pass[key] is not False for key in ("drawsReceiver", "claimsSelfShadow", "receiverComposition")):
        raise RuntimeError(
            "R32F caster pass unexpectedly claimed receiver/self-shadow composition: "
            f"{caster_pass!r}"
        )
    if (
        caster_pass["createAttemptCount"] < 1
        or caster_pass["createSucceeded"] is not True
        or caster_pass["releaseAttemptCount"] < 1
        or caster_pass["releaseSucceeded"] is not True
        or caster_pass["releaseBeforeTarget"] is not True
        or caster_pass["status"] != "released"
    ):
        raise RuntimeError(
            "R32F caster pass did not complete a successful create/release-before-target lifecycle: "
            f"{caster_pass!r}"
        )


def _validate_light_space_camera(camera: object) -> None:
    """Require a real directional-light camera lifecycle without parity claims."""
    required = {
        "enabled",
        "status",
        "reason",
        "source",
        "directionalLight",
        "cameraTransform",
        "cameraShape",
        "cameraPath",
        "roots",
        "boundsSource",
        "bounds",
        "center",
        "forward",
        "rotation",
        "distance",
        "orthographicWidth",
        "nearClip",
        "farClip",
        "createAttemptCount",
        "createSucceeded",
        "releaseAttemptCount",
        "releaseSucceeded",
    }
    if not isinstance(camera, dict) or not required.issubset(camera):
        raise RuntimeError(f"light-space camera diagnostic is missing: {camera!r}")
    if camera["source"] != "directional-light":
        raise RuntimeError(f"light-space camera has an invalid source: {camera!r}")
    bounds = camera["bounds"]
    if (
        camera["status"] != "released"
        or camera["createAttemptCount"] < 1
        or camera["createSucceeded"] is not True
        or camera["releaseAttemptCount"] < 1
        or camera["releaseSucceeded"] is not True
        or not isinstance(camera["directionalLight"], str)
        or not camera["directionalLight"]
        or not isinstance(camera["cameraPath"], str)
        or not camera["cameraPath"]
        or not isinstance(bounds, dict)
        or not isinstance(bounds.get("min"), list)
        or not isinstance(bounds.get("max"), list)
        or len(bounds["min"]) != 3
        or len(bounds["max"]) != 3
    ):
        raise RuntimeError(
            "light-space camera did not complete directional-light camera lifecycle: "
            f"{camera!r}"
        )


def _validate_r32f_receiver_probe(
    receiver_probe: object, *, require_caster_value: bool = False
) -> None:
    """Require quad bind/draw/readback, optionally proving a non-clear caster value."""
    required = {
        "enabled",
        "status",
        "reason",
        "inputTargetName",
        "outputTargetName",
        "shaderPath",
        "technique",
        "parameter",
        "outputTransform",
        "createAttemptCount",
        "createSucceeded",
        "bindAttemptCount",
        "bindSucceeded",
        "postDrawCallbackCount",
        "manualReadbackCount",
        "releaseAttemptCount",
        "releaseSucceeded",
        "releaseBeforeTarget",
        "drawsReceiver",
        "receiverComposition",
        "claimsSelfShadow",
        "output",
    }
    if not isinstance(receiver_probe, dict) or not required.issubset(receiver_probe):
        raise RuntimeError(f"R32F receiver-probe diagnostic is missing: {receiver_probe!r}")
    if any(
        receiver_probe[key] is not False
        for key in ("receiverComposition", "claimsSelfShadow")
    ) or receiver_probe["drawsReceiver"] is not True:
        raise RuntimeError(
            "R32F receiver probe unexpectedly changed self-shadow composition claims: "
            f"{receiver_probe!r}"
        )
    output = receiver_probe["output"]
    if (
        receiver_probe["status"] != "released"
        or receiver_probe["createAttemptCount"] < 1
        or receiver_probe["createSucceeded"] is not True
        or receiver_probe["bindAttemptCount"] < 1
        or receiver_probe["bindSucceeded"] is not True
        or receiver_probe["postDrawCallbackCount"] + receiver_probe["manualReadbackCount"] < 1
        or receiver_probe["releaseAttemptCount"] < 1
        or receiver_probe["releaseSucceeded"] is not True
        or receiver_probe["releaseBeforeTarget"] is not True
        or not isinstance(output, dict)
        or output.get("status") != "sampled"
        or output.get("readbackCount", 0) < 1
        or output.get("changedFromClear") is not True
        or output.get("nonClearSampleCount", 0) < 1
        or (
            require_caster_value
            and (
                not isinstance(output.get("minSample"), (int, float))
                or output.get("minSample") <= 1e-6
            )
        )
    ):
        raise RuntimeError(
            "R32F receiver probe did not complete shader bind/draw/readback/release: "
            f"{receiver_probe!r}"
        )


def _evaluate_target_probe_caster_selection(override, *, manual_readback: bool = True) -> None:
    """Invoke routing and post-render readback callbacks once in live Maya.

    Maya normally evaluates ``objectSetOverride`` while rendering.  Calling
    the callbacks explicitly after a forced refresh makes this diagnostic
    deterministic even when a GUI viewport is not repainting continuously.
    """
    if not override.startOperationIterator():
        raise RuntimeError("target probe operation iterator did not start")
    while True:
        operation = override.renderOperation()
        callback = getattr(operation, "objectSetOverride", None) if operation else None
        if callable(callback):
            callback()
            if manual_readback:
                readback = getattr(operation, "manual_target_occupancy", None)
                if callable(readback):
                    readback()
            return
        if not override.nextRenderOperation():
            break
    raise RuntimeError("target probe operation has no objectSetOverride callback")


def run_probe(
    log_path: str,
    report_path: str,
    output_dir: str,
    expected_draw_api_name: str | None = None,
    target_probe: bool = False,
    model_path: str | None = None,
    r32f_binding_probe: bool = False,
    r32f_caster_pass: bool = False,
    r32f_receiver_probe: bool = False,
    r32f_light_space_caster: bool = False,
) -> None:
    """Execute the R1 lifecycle and optional R2 target probe in live Maya.

    When ``model_path`` is supplied, import the real PMX through the
    production importer before evaluating the target caster-routing
    diagnostic.  Target occupancy is reported only as conservative D32/R32F
    readback evidence.  The optional R32F binding probe only records whether a
    plugin-owned ``MShaderInstance`` accepts the offscreen target; it performs
    no receiver composition or self-shadow parity assertion.  The explicit
    ``r32f_caster_pass`` option adds a dedicated plugin-owned HLSL caster
    shader, still without claiming receiver or self-shadow parity.  The
    explicit ``r32f_receiver_probe`` option samples that target with a
    separate ``MQuadRender`` output; it is a readback diagnostic, not MMD
    receiver composition.  The explicit ``r32f_light_space_caster`` option
    gives the caster operation a temporary orthographic camera aligned to the
    first directional light; it does not claim a complete shadow projection.
    """
    import os

    import maya.api.OpenMayaRender as omr
    import maya.api.OpenMayaUI as omui
    import maya.cmds as cmds

    log_file = Path(log_path)
    report_file = Path(report_path)
    output = Path(output_dir)
    report = {"status": "error", "checks": {}, "captures": {}, "errors": []}

    def log(message: str) -> None:
        with log_file.open("a", encoding="utf-8") as stream:
            stream.write(f"{message}\n")
        print(message)

    previous_renderer_by_panel = {}
    previous_override_by_panel = {}
    plugin_name = None
    plugin_loaded = False
    try:
        os.environ["MMD_TOOLS_ENABLE_RENDER_OVERRIDE"] = "1"
        os.environ["MMD_TOOLS_SKIP_SHADER_OVERRIDE"] = "1"
        os.environ["MMD_TOOLS_ENABLE_RENDER_OVERRIDE_TARGET_PROBE"] = (
            "1" if target_probe else "0"
        )
        os.environ["MMD_TOOLS_ENABLE_RENDER_OVERRIDE_R32F_BINDING_PROBE"] = (
            "1" if r32f_binding_probe else "0"
        )
        os.environ["MMD_TOOLS_ENABLE_RENDER_OVERRIDE_R32F_CASTER_PASS"] = (
            "1" if r32f_caster_pass else "0"
        )
        os.environ["MMD_TOOLS_ENABLE_RENDER_OVERRIDE_R32F_RECEIVER_PROBE"] = (
            "1" if r32f_receiver_probe else "0"
        )
        os.environ["MMD_TOOLS_ENABLE_RENDER_OVERRIDE_R32F_LIGHT_SPACE"] = (
            "1" if r32f_light_space_caster else "0"
        )
        cmds.file(new=True, force=True)
        previous_renderer_by_panel = _renderer_by_panel(cmds)
        report["checks"]["previousRendererByPanel"] = previous_renderer_by_panel
        report["checks"]["vp2RendererByPanel"] = _set_renderer_by_panel(cmds, "vp2Renderer")
        panels = list(report["checks"]["vp2RendererByPanel"])
        previous_override_by_panel = _override_by_panel(omui, panels)
        report["checks"]["previousOverrideByPanel"] = previous_override_by_panel
        report["checks"]["vp2Device"] = cmds.ogs(deviceInformation=True)
        report["checks"]["vp2DrawApi"] = omr.MRenderer.drawAPI()
        if expected_draw_api_name:
            expected_draw_api = getattr(omr.MRenderer, expected_draw_api_name)
            report["checks"]["expectedVp2DrawApi"] = {
                "name": expected_draw_api_name,
                "value": expected_draw_api,
            }
            report["checks"]["expectedVp2DrawApiActive"] = (
                report["checks"]["vp2DrawApi"] == expected_draw_api
            )
            if not report["checks"]["expectedVp2DrawApiActive"]:
                raise RuntimeError(
                    f"expected VP2 draw API {expected_draw_api_name}={expected_draw_api}, "
                    f"got {report['checks']['vp2DrawApi']}"
                )
        plugin_path = _ROOT / "plug-ins" / "mmd_tools_plugin.py"
        loaded = cmds.loadPlugin(str(plugin_path), quiet=True)
        plugin_name = loaded[0] if loaded else plugin_path.stem
        plugin_loaded = True

        from mmd_tools.view import render_override

        override = render_override.registered_override()
        if override is None:
            raise RuntimeError("R1 override was not registered by opt-in plugin load")
        renderer_after_load = _renderer_by_panel(cmds)
        report["checks"]["pluginLoadPreservedPanelRenderer"] = (
            renderer_after_load == report["checks"]["vp2RendererByPanel"]
        )
        if not report["checks"]["pluginLoadPreservedPanelRenderer"]:
            raise RuntimeError("plugin load changed a modelPanel renderer")

        if model_path is not None:
            model = Path(model_path)
            if not model.is_file():
                raise FileNotFoundError(f"PMX model not found: {model}")
            from mmd_tools.io.mmd_importer import import_mmd_file

            imported_root = import_mmd_file(
                str(model),
                options={
                    "create_mmd_shaders": False,
                    "import_physics": False,
                    "setup_rig": False,
                    "setup_bone_orientation": False,
                    "use_cpp_fast_load": False,
                    "use_native_pmx_parse": False,
                    "require_native_pmx_parse": False,
                },
            )
            if not imported_root:
                raise RuntimeError(f"PMX import returned no model root: {model}")
            report["checks"]["importedPmx"] = {
                "path": str(model),
                "root": str(imported_root),
            }
            # Ensure Maya has evaluated the imported DAG before the target
            # operation's objectSetOverride callback is inspected below.
            cmds.refresh(force=True)

        if not override.startOperationIterator():
            raise RuntimeError("R1 override operation iterator did not start")
        operation_types = []
        while True:
            operation = override.renderOperation()
            if operation is None:
                break
            operation_types.append(operation.operationType())
            if not override.nextRenderOperation():
                break
        expected_types = [
            omr.MSceneRender.kSceneRender,
            omr.MHUDRender.kHUDRender,
            omr.MPresentTarget.kPresentTarget,
        ]
        if target_probe:
            expected_types.insert(0, omr.MSceneRender.kSceneRender)
        if r32f_receiver_probe:
            expected_types.insert(1, omr.MQuadRender.kQuadRender)
        report["checks"]["operationTypes"] = operation_types
        report["checks"]["operationOrder"] = operation_types == expected_types
        override.cleanup()
        if not report["checks"]["operationOrder"]:
            raise RuntimeError(f"unexpected R1 operation order: {operation_types!r}")

        cube = cmds.polyCube(name="renderOverrideParityCube", width=2.0, height=2.0, depth=2.0)[0]
        cmds.setAttr(f"{cube}.translateY", 0.5)
        light_shape = cmds.directionalLight(name="renderOverrideParityLight", intensity=1.0)
        light_xform = cmds.listRelatives(light_shape, parent=True)[0]
        cmds.setAttr(f"{light_xform}.rotateX", -45.0)
        cmds.setAttr(f"{light_xform}.rotateY", -30.0)
        cmds.viewFit("persp", all=True, fitFactor=0.8)
        cmds.refresh(force=True)

        baseline = _capture_current_view(cmds, output / "baseline.png")
        report["captures"]["baseline"] = str(baseline)

        selected_by_panel = _set_override_by_panel(omui, render_override.RENDER_OVERRIDE_NAME, panels)
        report["checks"]["registeredOverrideName"] = override.name()
        report["checks"]["registeredOverrideCount"] = omr.MRenderer.renderOverrideCount()
        report["checks"]["supportedDrawAPIs"] = override.supportedDrawAPIs()
        report["checks"]["overrideByPanel"] = selected_by_panel
        report["checks"]["overrideActivated"] = all(
            name == render_override.RENDER_OVERRIDE_NAME for name in selected_by_panel.values()
        )
        if not report["checks"]["overrideActivated"]:
            raise RuntimeError("R1 override could not become active in the GUI viewport")
        if target_probe:
            # Maya may invoke ``cleanup`` after a viewport refresh.  Evaluate
            # the caster selection while the operation still owns its shader
            # and targets; the actual render below remains the source of
            # occupancy evidence.
            _evaluate_target_probe_caster_selection(override, manual_readback=False)
        cmds.refresh(force=True)
        overridden = _capture_current_view(cmds, output / "override.png")
        report["captures"]["override"] = str(overridden)
        if target_probe:
            override.cleanup()
            target_report = override.target_probe_report()
            report["checks"]["targetProbe"] = target_report
            caster_selection = (target_report or {}).get("casterSelection")
            report["checks"]["targetProbeCasterSelection"] = caster_selection
            _validate_target_probe_caster_selection(
                caster_selection, require_components=model_path is not None
            )
            if target_report is None or not target_report["balanced"]:
                raise RuntimeError(f"unbalanced R2 target resources: {target_report}")
            _validate_target_probe_occupancy(
                target_report, require_components=model_path is not None
            )
            if r32f_binding_probe:
                _validate_r32f_binding_probe(
                    (target_report or {}).get("r32fBindingProbe")
                )
            if r32f_caster_pass:
                _validate_r32f_caster_pass(
                    (target_report or {}).get("r32fCasterPass")
                )
            if r32f_light_space_caster:
                _validate_light_space_camera(
                    (target_report or {}).get("lightSpaceCamera")
                )
            if r32f_receiver_probe:
                _validate_r32f_receiver_probe(
                    (target_report or {}).get("r32fReceiverProbe"),
                    require_caster_value=r32f_light_space_caster,
                )

        restored_by_panel = {}
        for panel, override_name in previous_override_by_panel.items():
            restored_by_panel[panel] = _set_override_by_panel(omui, override_name or "", [panel])[panel]
        report["checks"]["overrideRestored"] = (
            restored_by_panel == previous_override_by_panel
        )
        if not report["checks"]["overrideRestored"]:
            raise RuntimeError("failed to restore the prior modelPanel render override")
        report["status"] = "pass"
    except Exception:
        report["errors"].append(traceback.format_exc())
        log(f"EXCEPTION:\n{report['errors'][-1]}")
    finally:
        try:
            if previous_renderer_by_panel:
                restored = _restore_renderer_by_panel(cmds, previous_renderer_by_panel)
                report["checks"]["originalPanelRendererRestored"] = restored == previous_renderer_by_panel
        except Exception as exc:
            report["errors"].append(f"panel renderer restore failed: {exc}")
            report["status"] = "error"
        if plugin_loaded:
            try:
                cmds.unloadPlugin(plugin_name or "mmd_tools_plugin", force=True)
            except Exception as exc:
                report["errors"].append(f"plugin unload failed: {exc}")
                report["status"] = "error"
        _write_report(report_file, report)
        log(f"RESULT_JSON: {json.dumps(report, ensure_ascii=False, sort_keys=True)}")
        log(COMPLETION_MARKER)


def _paeth(left: int, up: int, up_left: int) -> int:
    """Return the PNG Paeth predictor without relying on image libraries."""
    estimate = left + up - up_left
    left_distance = abs(estimate - left)
    up_distance = abs(estimate - up)
    up_left_distance = abs(estimate - up_left)
    if left_distance <= up_distance and left_distance <= up_left_distance:
        return left
    if up_distance <= up_left_distance:
        return up
    return up_left


def _read_png_rgb(path: Path) -> tuple[int, int, bytes]:
    """Read an 8-bit RGB/RGBA PNG as packed RGB bytes using only stdlib."""
    content = path.read_bytes()
    if content[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"not a PNG: {path}")
    offset = 8
    width = height = bit_depth = color_type = None
    compressed = bytearray()
    while offset < len(content):
        length = struct.unpack(">I", content[offset : offset + 4])[0]
        chunk_type = content[offset + 4 : offset + 8]
        chunk_data = content[offset + 8 : offset + 8 + length]
        offset += 12 + length
        if chunk_type == b"IHDR":
            width, height, bit_depth, color_type, compression, filter_method, interlace = struct.unpack(
                ">IIBBBBB", chunk_data
            )
            if compression != 0 or filter_method != 0 or interlace != 0:
                raise ValueError(f"unsupported PNG encoding: {path}")
        elif chunk_type == b"IDAT":
            compressed.extend(chunk_data)
        elif chunk_type == b"IEND":
            break
    if width is None or height is None or bit_depth != 8 or color_type not in {2, 6}:
        raise ValueError(f"unsupported PNG format: {path}")
    bytes_per_pixel = 3 if color_type == 2 else 4
    stride = width * bytes_per_pixel
    raw = zlib.decompress(compressed)
    expected_size = height * (stride + 1)
    if len(raw) != expected_size:
        raise ValueError(f"unexpected PNG data size: {path}")
    previous = bytearray(stride)
    rgb = bytearray(width * height * 3)
    raw_offset = rgb_offset = 0
    for _ in range(height):
        filter_type = raw[raw_offset]
        raw_offset += 1
        current = bytearray(raw[raw_offset : raw_offset + stride])
        raw_offset += stride
        for index, value in enumerate(current):
            left = current[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
            up = previous[index]
            up_left = previous[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
            if filter_type == 1:
                current[index] = (value + left) & 0xFF
            elif filter_type == 2:
                current[index] = (value + up) & 0xFF
            elif filter_type == 3:
                current[index] = (value + ((left + up) >> 1)) & 0xFF
            elif filter_type == 4:
                current[index] = (value + _paeth(left, up, up_left)) & 0xFF
            elif filter_type != 0:
                raise ValueError(f"unsupported PNG filter {filter_type}: {path}")
        for pixel in range(width):
            start = pixel * bytes_per_pixel
            rgb[rgb_offset : rgb_offset + 3] = current[start : start + 3]
            rgb_offset += 3
        previous = current
    return width, height, bytes(rgb)


def _compare_captures(reference: Path, candidate: Path) -> dict:
    """Return a strict same-scene RGB comparison for R1 passthrough parity."""
    reference_width, reference_height, reference_rgb = _read_png_rgb(reference)
    candidate_width, candidate_height, candidate_rgb = _read_png_rgb(candidate)
    if (reference_width, reference_height) != (candidate_width, candidate_height):
        return {
            "pass": False,
            "reason": "size-mismatch",
            "referenceSize": [reference_width, reference_height],
            "candidateSize": [candidate_width, candidate_height],
        }
    deltas = [abs(left - right) for left, right in zip(reference_rgb, candidate_rgb)]
    max_delta = max(deltas, default=0)
    mean_delta = sum(deltas) / len(deltas) if deltas else 0.0
    return {
        "pass": max_delta <= 2 and mean_delta <= 0.1,
        "maxRgbDelta": max_delta,
        "meanRgbDelta": mean_delta,
    }


def main() -> int:
    """Launch Maya GUI, run the probe, then apply the host-side pixel gate."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maya", default="2024")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--vp2-device", choices=("default", "dx11", "gl", "glcore"), default="default")
    parser.add_argument("--target-probe", action="store_true")
    parser.add_argument(
        "--r32f-binding-probe",
        action="store_true",
        help=(
            "Record only plugin-owned MShaderInstance R32F setParameter/release "
            "lifecycle diagnostics; does not draw a receiver."
        ),
    )
    parser.add_argument(
        "--r32f-caster-pass",
        action="store_true",
        help=(
            "Use the opt-in plugin-owned MMD caster HLSL for the R32F/D32 "
            "target pair; does not draw a receiver or claim self-shadow parity."
        ),
    )
    parser.add_argument(
        "--r32f-receiver-probe",
        action="store_true",
        help=(
            "Sample the caster R32F target with a separate MQuadRender output; "
            "does not compose MMD self-shadowing."
        ),
    )
    parser.add_argument(
        "--r32f-light-space-caster",
        action="store_true",
        help=(
            "Give the caster operation a temporary orthographic camera aligned "
            "to a directional light; does not claim shadow parity."
        ),
    )
    parser.add_argument(
        "--model",
        type=Path,
        default=None,
        help="Optional PMX model to import for real caster-selection routing evidence.",
    )
    parser.add_argument("--out-dir", type=Path, default=_ROOT / "build" / "render-override-e2e")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    args = parser.parse_args()
    if args.model is not None and not args.target_probe:
        parser.error("--model requires --target-probe")
    if args.r32f_binding_probe and not args.target_probe:
        parser.error("--r32f-binding-probe requires --target-probe")
    if args.r32f_caster_pass and not args.target_probe:
        parser.error("--r32f-caster-pass requires --target-probe")
    if args.r32f_receiver_probe and not args.r32f_caster_pass:
        parser.error("--r32f-receiver-probe requires --r32f-caster-pass")
    if args.r32f_light_space_caster and not args.r32f_caster_pass:
        parser.error("--r32f-light-space-caster requires --r32f-caster-pass")

    out_dir = args.out_dir.resolve()
    log_path = out_dir / f"render_override_maya{args.maya}.log"
    report_path = out_dir / f"render_override_maya{args.maya}.json"
    vp2_device_overrides = {
        "dx11": "VirtualDeviceDx11",
        "gl": "VirtualDeviceGL",
        "glcore": "VirtualDeviceGLCore",
    }
    expected_draw_api_name = {
        "dx11": "kDirectX11",
        "gl": "kOpenGL",
        "glcore": "kOpenGLCoreProfile",
    }.get(args.vp2_device)
    model_literal = (
        "None"
        if args.model is None
        else json.dumps(str(args.model.resolve()), ensure_ascii=True)
    )
    command = (
        "from tools.render_override_e2e import run_probe\n"
        f"run_probe(r'{log_path.as_posix()}', r'{report_path.as_posix()}', r'{out_dir.as_posix()}', {expected_draw_api_name!r}, {args.target_probe!r}, {model_literal}, {args.r32f_binding_probe!r}, {args.r32f_caster_pass!r}, {args.r32f_receiver_probe!r}, {args.r32f_light_space_caster!r})\n"
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
        send_label="<render-override-e2e-command>",
        stale_paths=(log_path, report_path, out_dir / "baseline.png", out_dir / "override.png"),
        port_error=f"commandPort :{args.port} is already open; choose another --port",
        report_error=f"render override report missing: {report_path}",
        log_ready=LOGGER,
        warn_detached=True,
        env_overrides=(
            {"MAYA_VP2_DEVICE_OVERRIDE": vp2_device_overrides[args.vp2_device]}
            if args.vp2_device in vp2_device_overrides
            else None
        ),
    )
    if report.get("status") == "pass":
        comparison = _compare_captures(Path(report["captures"]["baseline"]), Path(report["captures"]["override"]))
        report["captureParity"] = comparison
        if not comparison.get("pass"):
            report["status"] = "fail"
            report["errors"].append(f"R1 capture parity failed: {comparison}")
        _write_report(report_path, report)
    LOGGER.info("R1 GUI gate status: %s", report.get("status"))
    return 0 if report.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
